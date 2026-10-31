"""Shared trajectory query grammar, SQLite predicates, and search projections."""

from __future__ import annotations

import fnmatch
import re
from functools import lru_cache

from .generation_state import AUTOMATED_GENERATION_SQL, GENERATION_PHASES, generation_matches

STATES = {
    "unfinished": -3, "rejected": -2, "unsuccessful": -1,
    "pending": 0, "candidate": 1, "validated": 2, "ready": 3,
}


class SearchSyntaxError(ValueError):
    def __init__(self, message):
        super().__init__(f"Invalid search query: {message}")


def readable_text(sample):
    """Index values, never JSON field names or serialized JSON escaping."""
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, child in value.items():
                if key == "segments" and isinstance(child, list):
                    # Loss-mask boundaries can split a word in displayed text.
                    yield "".join(segment["text"] for segment in child
                                  if isinstance(segment, dict) and isinstance(segment.get("text"), str))
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    return "\n".join(strings(sample)).casefold()


def used_tools(sample):
    """Actual authored calls, including invisible calls; advertisements are not usage."""
    replay = sample.get("replay")
    if not isinstance(replay, dict):
        return set()
    return {
        event["tool"].casefold()
        for group in replay.values() if isinstance(group, list)
        for event in group if isinstance(event, dict)
        if event.get("op") in ("tool-call", "invisible-tool-call")
        and isinstance(event.get("tool"), str)
    }


@lru_cache(maxsize=512)
def pattern_regex(pattern, words=False):
    # Only * is special. fnmatch's atomic groups avoid exponential backtracking
    # on patterns with many stars. All other regex/glob metacharacters are data.
    escaped = "".join({"[": "[[]", "]": "[]]", "?": "[?]"}.get(c, c) for c in pattern)
    expression = fnmatch.translate(escaped)
    if words:
        expression = r"(?<!\w)" + expression[:-2].replace(".*", r"\w*") + r"(?!\w)"
    return re.compile(expression)


def glob_match(pattern, value):
    return value is not None and pattern_regex(pattern).fullmatch(str(value).casefold()) is not None


def text_match(pattern, value):
    return bool(pattern_regex(pattern, words=True).search(value))


def tag_match(pattern, value):
    return glob_match(pattern, value.removeprefix("#"))


def register_search_functions(db):
    db.create_function("search_glob", 2, glob_match, deterministic=True)
    db.create_function("search_text", 2, text_match, deterministic=True)
    db.create_function("search_tag", 2, tag_match, deterministic=True)


def diagnostic_flags(sample, compiler):
    """Use the same diagnostics as validation, without pinning tool dependencies."""
    from .compiler import CompileError

    try:
        plan = compiler.compile(sample, record_dependencies=False)
    except CompileError:
        # The compiler stops at the first error, before producing warnings.
        return True, False
    return False, any(item["severity"] == "warning" for item in plan["diagnostics"])


def tokenize(query):
    tokens, index = [], 0
    while index < len(query):
        char = query[index]
        if char.isspace():
            index += 1
            continue
        if char in "()|-":
            tokens.append((char, char))
            index += 1
        else:
            value = []
            while index < len(query) and not query[index].isspace() and query[index] not in "()|":
                if query[index] == '"':
                    index += 1
                    while index < len(query) and query[index] != '"':
                        value.append(query[index])
                        index += 1
                    if index == len(query):
                        raise SearchSyntaxError('missing closing double quote')
                    index += 1
                else:
                    value.append(query[index])
                    index += 1
            if not value:
                raise SearchSyntaxError("empty search term")
            tokens.append(("term", "".join(value).casefold()))
        if len(tokens) > 256:
            raise SearchSyntaxError("use at most 256 tokens")
    return tokens


class SearchQuery:
    """Parse once, then compile to parameterized SQL or match an in-memory draft.

    Precedence: unary negation, implicit AND, then OR. Parentheses override it.
    """

    def __init__(self, query):
        if len(query) > 4096:
            raise SearchSyntaxError("use at most 4096 characters")
        self.tokens = tokenize(query)
        self.index = 0
        self.requires_diagnostics = False
        self.root = self._or() if self.tokens else ("all",)
        if self.index != len(self.tokens):
            raise SearchSyntaxError(f"unexpected {self.tokens[self.index][1]!r}")

    def _peek(self):
        return self.tokens[self.index][0] if self.index < len(self.tokens) else None

    def _or(self, depth=0):
        nodes = [self._and(depth)]
        while self._peek() == "|":
            self.index += 1
            nodes.append(self._and(depth))
        return nodes[0] if len(nodes) == 1 else ("or", nodes)

    def _and(self, depth):
        nodes = []
        while self._peek() not in (None, "|", ")"):
            nodes.append(self._unary(depth))
        if not nodes:
            raise SearchSyntaxError("expected a term or parenthesized expression")
        return nodes[0] if len(nodes) == 1 else ("and", nodes)

    def _unary(self, depth):
        if depth > 32:
            raise SearchSyntaxError("use at most 32 levels of nesting/negation")
        kind = self._peek()
        if kind == "-":
            self.index += 1
            return ("not", self._unary(depth + 1))
        if kind == "(":
            self.index += 1
            node = self._or(depth + 1)
            if self._peek() != ")":
                raise SearchSyntaxError("missing closing parenthesis")
            self.index += 1
            return node
        if kind != "term":
            raise SearchSyntaxError("expected a term after negation")
        value = self.tokens[self.index][1]
        self.index += 1
        if value.startswith("#"):
            if len(value) == 1:
                raise SearchSyntaxError("supply a tag after #")
            return ("tag", value[1:])
        if value.startswith("@"):
            name, separator, value = value[1:].partition(":")
            if name not in ("name", "uuid", "windows", "uses_tool", "state", "batch", "generation", "errors", "warnings"):
                raise SearchSyntaxError(f"unknown property @{name}; use @name, @uuid, @windows, @uses_tool, @state, @batch, @generation, @errors, or @warnings")
            if not separator or not value:
                raise SearchSyntaxError(f"supply a value after @{name}:")
            if name in ("errors", "warnings"):
                if value not in ("true", "false"):
                    raise SearchSyntaxError(f"@{name} requires true or false")
                self.requires_diagnostics = True
                return (name, value == "true")
            if name == "state":
                levels = {str(level): level for level in STATES.values()}
                if value not in STATES and value not in levels:
                    raise SearchSyntaxError("@state requires unfinished, rejected, unsuccessful, pending, candidate, validated, ready, or a level from -3 to 3")
                return ("state", STATES[value] if value in STATES else levels[value])
            if name == 'generation':
                if value not in GENERATION_PHASES:
                    raise SearchSyntaxError('@generation requires unfinished or complete')
                return ('generation', GENERATION_PHASES[value])
            if name == "windows":
                match = re.fullmatch(r"(>=|<=|!=|=|>|<)?([0-9]+)", value)
                if not match:
                    raise SearchSyntaxError("@windows requires a nonnegative integer and optional =, !=, <, <=, >, or >=")
                number = int(match[2])
                if number > 2**63 - 1:
                    raise SearchSyntaxError("@windows value is too large")
                return ("windows", match[1] or "=", number)
            return (name, value)
        return ("text", value)

    def sql(self):
        """Return a predicate and bound values for samples s / trajectory_metadata m."""
        args = []

        def compile_node(node):
            kind = node[0]
            if kind == "all":
                return "1"
            if kind in ("and", "or"):
                return "(" + f" {kind.upper()} ".join(compile_node(n) for n in node[1]) + ")"
            if kind == "not":
                return f"(NOT {compile_node(node[1])})"
            if kind == "windows":
                args.append(node[2])
                return f"m.window_count {node[1]} ?"
            args.append(node[1])
            if kind in ("errors", "warnings"):
                return f"(SELECT d.{kind} FROM trajectory_search_diagnostics d WHERE d.trajectory_uuid=s.uuid) = ?"
            if kind == "state":
                return "COALESCE(json_extract(m.properties, '$.validation_level'), -3) = ?"
            if kind == 'generation':
                return f"({AUTOMATED_GENERATION_SQL} AND json_extract(m.properties,'$.generation_phase') IS ?)"
            if kind == "batch":
                return "search_glob(?, json_extract(m.properties, '$.generation_batch'))"
            if kind == "text":
                return "search_text(?, m.search)" if "*" in node[1] else "instr(m.search, ?) > 0"
            if kind in ("name", "uuid"):
                return f"search_glob(?, s.{'sample_id' if kind == 'name' else 'uuid'})"
            if kind == "tag":
                return "EXISTS (SELECT 1 FROM metadata_tags t WHERE t.trajectory_uuid=s.uuid AND search_tag(?, t.tag))"
            return "EXISTS (SELECT 1 FROM trajectory_search_tools t WHERE t.trajectory_uuid=s.uuid AND search_glob(?, t.tool))"

        return compile_node(self.root), args

    def matches(self, sample, compiler=None, *, store=None):
        """Match authoring drafts; diagnostic properties need the project compiler."""
        from .compiler import window_names
        from .dataset import trajectory_uuid

        flags = None

        def evaluate(node):
            nonlocal flags
            kind = node[0]
            if kind == "all":
                return True
            if kind == "and":
                return all(evaluate(n) for n in node[1])
            if kind == "or":
                return any(evaluate(n) for n in node[1])
            if kind == "not":
                return not evaluate(node[1])
            if kind in ("errors", "warnings"):
                if compiler is None:
                    raise ValueError("Diagnostic search requires a compiler")
                if flags is None:
                    flags = diagnostic_flags(sample, compiler)
                return flags[0 if kind == "errors" else 1] == node[1]
            if kind == "text":
                text = readable_text(sample)
                return text_match(node[1], text) if "*" in node[1] else node[1] in text
            if kind == "name":
                return glob_match(node[1], sample.get("id"))
            if kind == "uuid":
                return glob_match(node[1], trajectory_uuid(sample))
            if kind == 'generation':
                return generation_matches(sample, node[1], store or getattr(compiler, 'tool_store', None))
            if kind in ("state", "batch"):
                metadata = sample.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                if kind == "state":
                    level = metadata.get("validation_level")
                    return (level if level is not None else -3) == node[1]
                return glob_match(node[1], metadata.get("generation_batch"))
            if kind == "tag":
                metadata = sample.get("metadata")
                tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
                return isinstance(tags, list) and any(tag_match(node[1], tag) for tag in tags if isinstance(tag, str))
            if kind == "uses_tool":
                return any(glob_match(node[1], tool) for tool in used_tools(sample))
            count, target = len(window_names(sample)), node[2]
            return {"=": count == target, "!=": count != target, "<": count < target,
                    "<=": count <= target, ">": count > target, ">=": count >= target}[node[1]]

        return evaluate(self.root)
