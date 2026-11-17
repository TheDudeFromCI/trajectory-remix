"""Read, check and save a project's Flow for the visual Flow editor.

The flow lives in the project database (see flow_store). The editor works on the same
normalized Flow the interpreter freezes into a run. Saving is guarded by a revision (a
digest of the saved text) so two tabs cannot silently overwrite each other. Diagnostics name the node and field they refer to so the editor
can mark the right box on the chart.
"""

from importlib.resources import files

import yaml
from pydantic import ValidationError

from .adaptive_flow import Flow, FlowLoader
from .flow_store import PACKAGED, FlowConflict, project_text, revision, write
from .models import digest

__all__ = ["FlowConflict", "check", "read_document", "render", "save"]
KINDS = ("select", "event", "branch", "end", "tools")
# Fields the interpreter reads for each kind; others are defaults and are not written.
FIELDS = {
    "select": ("kind", "instruction", "next"),
    "event": ("kind", "instruction", "next"),
    "branch": ("kind", "path", "equals", "then", "otherwise"),
    "end": ("kind",),
    "tools": ("kind", "instruction", "next", "extra_tools"),
}
DEFAULTS = dict(next=None, path="finished", equals=True, then=None, otherwise=None, extra_tools=0.5)
TOP = (
    "version",
    "start",
    "max_steps",
    "max_events",
    "immediate_response",
    "actor_instruction",
    "selector_instruction",
    "nodes",
)
SELECTION_FIELDS = {"finished", "reason", "windows", "task"}


def read_document(store):
    """The flow as runs will load it, plus its raw text and revision."""
    saved = project_text(store)
    text = packaged_text() if saved is None else saved
    return dict(
        check(text=text),
        text=text,
        origin=PACKAGED if saved is None else "project",
        revision=revision(saved),
    )


def packaged_document():
    """The default flow that ships with Remix, checked, for the editor to load as a draft."""
    text = packaged_text()
    return dict(check(text=text), text=text, origin=PACKAGED)


def packaged_text():
    return files(__package__).joinpath("flow.yaml").read_text()


def _diag(severity, code, message, node=None, field=None):
    return dict(severity=severity, code=code, message=message, node=node, field=field)


def _parse(text):
    if not isinstance(text, str):
        return None, [_diag("error", "yaml_type", "Flow YAML must be text")]
    if len(text.encode()) > 262144:
        return None, [_diag("error", "yaml_size", "Flow YAML exceeds 256 KiB")]
    try:
        return yaml.load(text, Loader=FlowLoader), []
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return None, [_diag("error", "yaml_syntax", f"{error.problem or 'Invalid YAML'}{where}")]
    except (yaml.YAMLError, ValueError, RecursionError) as error:
        return None, [_diag("error", "yaml_syntax", str(error) or "Invalid YAML")]


def check(flow=None, text=None):
    """Diagnose a draft without raising. Returns the normalized flow when it is valid."""
    if text is not None:
        flow, problems = _parse(text)
        if problems:
            return dict(valid=False, diagnostics=problems, flow=None, flow_hash=None, yaml=None)
    problems = structure(flow)
    normalized = None
    if not any(p["severity"] == "error" for p in problems):
        try:
            normalized = Flow.model_validate(flow).model_dump(mode="json")
        except ValidationError as error:
            for item in error.errors(include_context=False, include_input=False):
                where = ".".join(str(part) for part in item["loc"])
                message = f"{where}: {item['msg']}" if where else item["msg"]
                problems.append(_diag("error", "invalid_field", message))
    valid = normalized is not None
    return dict(
        valid=valid,
        diagnostics=problems,
        flow=normalized,
        flow_hash=digest(normalized) if valid else None,
        yaml=render(normalized) if valid else None,
    )


def structure(flow):
    """Field and graph checks with node locations. Pydantic stays the final authority."""
    if not isinstance(flow, dict):
        return [_diag("error", "not_mapping", "The flow must be a mapping of fields")]
    problems = []
    for key in set(flow) - set(TOP):
        problems.append(_diag("error", "unknown_field", f"Unknown flow field: {key}", field=key))
    if flow.get("version", "adaptive-flow/1") != "adaptive-flow/1":
        problems.append(_diag("error", "version", "Only adaptive-flow/1 is supported"))
    for key, top in (("max_steps", 10000), ("max_events", 2000)):
        value = flow.get(key, 1)
        if type(value) is not int or not 1 <= value <= top:
            problems.append(_diag("error", "limit", f"{key} must be 1–{top}", field=key))
    if not isinstance(flow.get("immediate_response", True), bool):
        text = "immediate_response must be true or false"
        problems.append(_diag("error", "immediate_response", text, field="immediate_response"))
    actor = flow.get("actor_instruction")
    if not isinstance(actor, str) or not actor.strip():
        text = "Actor instruction is required"
        problems.append(_diag("error", "actor_instruction", text, field="actor_instruction"))
    if not isinstance(flow.get("selector_instruction", ""), str):
        text = "Selector instruction must be text"
        problems.append(_diag("error", "selector_instruction", text, field="selector_instruction"))
    nodes = flow.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        problems.append(_diag("error", "no_nodes", "Add at least one node", field="nodes"))
        return problems
    if flow.get("start") not in nodes:
        problems.append(_diag("error", "start", "Choose a start node", field="start"))
    for ident, node in nodes.items():
        problems.extend(_node_problems(ident, node, nodes))
    if not any(p["severity"] == "error" for p in problems):
        problems.extend(_graph_warnings(flow))
    return problems


def _node_problems(ident, node, nodes):
    if not isinstance(ident, str) or not ident.strip():
        return [_diag("error", "node_id", "Node IDs must be nonempty text", node=str(ident))]
    if not isinstance(node, dict):
        return [_diag("error", "node_type", "A node must be a mapping", node=ident)]
    kind = node.get("kind")
    if kind not in KINDS:
        return [_diag("error", "kind", f"Unknown node kind: {kind}", node=ident, field="kind")]
    problems = []
    known = {"kind", "instruction", "next", "path", "equals", "then", "otherwise", "extra_tools"}
    for key in set(node) - known:
        problems.append(_diag("error", "unknown_field", f"Unknown field {key}", ident, key))
    for key in sorted(set(node) & known - set(FIELDS[kind]) - {"instruction"}):
        default = DEFAULTS[key]
        if type(node[key]) is not type(default) or node[key] != default:
            problems.append(_diag("warning", "ignored_field", f"{kind} ignores {key}", ident, key))
    targets = {
        "select": ("next",),
        "event": ("next",),
        "tools": ("next",),
        "branch": ("then", "otherwise"),
    }
    for key in targets.get(kind, ()):
        if node.get(key) not in nodes:
            text = f"{key} must point to an existing node"
            problems.append(_diag("error", "missing_target", text, ident, key))
    if kind == "tools":
        chance = node.get("extra_tools", 0.5)
        if type(chance) not in (int, float) or not 0 <= chance <= 1:
            text = "Use a chance from 0 to 1 for adding unchosen tools"
            problems.append(_diag("error", "extra_tools", text, ident, "extra_tools"))
    if kind in ("select", "tools") and not str(node.get("instruction", "")).strip():
        text = "The selector has no system prompt"
        problems.append(_diag("warning", "empty_instruction", text, ident, "instruction"))
    if kind == "branch":
        path = node.get("path", "finished")
        if not isinstance(path, str) or not path or any(not p for p in path.split(".")):
            problems.append(_diag("error", "path", "Use a dotted path", ident, "path"))
        elif path.split(".")[0] not in SELECTION_FIELDS | {"flow"}:
            text = (
                "Paths start with finished, reason, windows or task (the last selection),"
                " or flow (flow settings); this always fails"
            )
            problems.append(_diag("warning", "path_root", text, ident, "path"))
    return problems


def _edges(node):
    if node["kind"] in ("select", "event", "tools"):
        return [node.get("next")]
    if node["kind"] == "branch":
        return [node.get("then"), node.get("otherwise")]
    return []


def _graph_warnings(flow):
    nodes, start, problems = flow["nodes"], flow["start"], []
    seen, todo = set(), [start]
    while todo:
        ident = todo.pop()
        if ident not in seen:
            seen.add(ident)
            todo.extend(_edges(nodes[ident]))
    for ident in nodes:
        if ident not in seen:
            problems.append(_diag("warning", "unreachable", "Never reached from start", ident))
    if not any(nodes[i]["kind"] == "end" for i in seen):
        text = "No end node is reachable; runs can only stop at a limit"
        problems.append(_diag("warning", "no_end", text, field="nodes"))
    parents = {ident: [] for ident in nodes}
    for ident, node in nodes.items():
        for target in _edges(node):
            parents[target].append(ident)
    for ident, node in nodes.items():
        if node["kind"] == "end":
            problems.extend(_end_warnings(ident, parents[ident], nodes))
        if node["kind"] == "event" and _event_without_select(ident, nodes, start):
            text = "Can run without a fresh selection; it would rewrite the previous event"
            problems.append(_diag("warning", "stale_task", text, ident))
    if _branch_cycle(nodes):
        text = "Branches loop without selecting or writing; runs spin until max_steps"
        problems.append(_diag("warning", "branch_cycle", text, field="nodes"))
    return problems


def _end_warnings(ident, parents, nodes):
    for parent in parents:
        node = nodes[parent]
        guarded = (
            node["kind"] == "branch"
            and node.get("path", "finished") == "finished"
            and node.get("equals", True) is True
            and node.get("then") == ident
            and node.get("otherwise") != ident
        )
        if not guarded:
            text = f"Reached from {parent} without a finished selection; the run would stop"
            return [_diag("warning", "unguarded_end", text, ident)]
    return []


def _event_without_select(ident, nodes, start):
    """True if this event can be reached from start or another event with no select between."""
    sources = [start] + [i for i, n in nodes.items() if n["kind"] == "event"]
    for origin in sources:
        todo = [origin] if origin == start else _edges(nodes[origin])
        seen = set()
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            if current == ident:
                return True
            if nodes[current]["kind"] in ("branch",):
                todo.extend(_edges(nodes[current]))
    return False


def _branch_cycle(nodes):
    branches = {i for i, n in nodes.items() if n["kind"] == "branch"}
    state = {}

    def visit(ident):
        state[ident] = "open"
        for target in _edges(nodes[ident]):
            if target in branches and (
                state.get(target) == "open" or (target not in state and visit(target))
            ):
                return True
        state[ident] = "done"
        return False

    return any(ident not in state and visit(ident) for ident in branches)


class _Dumper(yaml.SafeDumper):
    pass


def _string(dumper, value):
    style = None
    if "\n" in value and not any(line != line.rstrip() for line in value.split("\n")):
        style = "|"
    elif len(value) > 80 and value == value.strip() and "  " not in value:
        style = ">"
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _string)


def compact(flow):
    """The normalized flow with only the fields each node kind uses, in a stable order."""
    result = {key: flow[key] for key in TOP if key != "nodes" and key in flow}
    if not result.get("selector_instruction"):
        result.pop("selector_instruction", None)
    result["nodes"] = {}
    for ident, node in flow["nodes"].items():
        kept = {key: node[key] for key in FIELDS[node["kind"]] if key in node}
        if node["kind"] == "event" or node.get("instruction"):
            kept.setdefault("instruction", node.get("instruction", ""))
        result["nodes"][ident] = kept
    return result


def render(flow):
    """YAML text for a normalized flow. Falls back to plain quoting if styles would lose text."""
    data = compact(flow)
    options = dict(sort_keys=False, allow_unicode=True, width=88, default_flow_style=False)
    text = yaml.dump(data, Dumper=_Dumper, **options)
    if yaml.load(text, Loader=FlowLoader) != data:
        text = yaml.safe_dump(data, **options)
    return text


def save(store, expected_revision, flow=None, text=None):
    """Store after checking the revision. Raw text is kept verbatim (comments survive)."""
    result = check(flow=flow, text=text)
    if not result["valid"]:
        first = next(p for p in result["diagnostics"] if p["severity"] == "error")
        raise ValueError("Flow not saved: " + first["message"])
    write(store, text if text is not None else result["yaml"], expected_revision)
    return read_document(store)
