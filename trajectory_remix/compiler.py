"""Tokenizer-aware compiler for Kidra Replay Specification 1.0.

All ids are tokenizer-native. Anchors and segment boundaries are checked against
one encoding of the entire visible content, never separately tokenized fragments.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass

from tokenizers import Tokenizer

from .dataset import trajectory_uuid
from .tools import ToolError, default_registry, tool_message, tool_request, tool_text

ANCHOR = re.compile(r"\{N_TICK:(\d+)\}")
WINDOW = re.compile(r"\{WINDOW:([^{}\r\n]+)\}")
KEY = re.compile(r"(?:\d+|\{N_TICK:(\d+)\}(?:\+(\d+))?)\Z")
MAX_TICKS = 100_000
MAX_ROWS = 500_000


class FrameIndex:
    """Server-side references to durable rows; implicit NOOPs need no replay work.

    Kept outside the compiled plan so training exports and plan serialization stay
    unchanged. Removal times remain on the original rows, including tool writes.
    """

    def __init__(self, plan):
        self.rows = defaultdict(list)
        self.ticks = defaultdict(list)
        for row in plan["rows"]:
            if row["durable"]:
                self.rows[row["window"]].append(row)
                self.ticks[row["window"]].append(row["tick"])

    def through(self, tick, windows):
        for window in windows:
            rows = self.rows.get(window, ())
            end = bisect_right(self.ticks.get(window, ()), tick)
            for i in range(end):
                yield rows[i]


def tick_terms(expression, depth=0):
    """Parse a bounded max/anchor grammar without evaluating Python."""
    if not isinstance(expression, str) or len(expression) > 8192 or depth > 16:
        raise ValueError("Invalid tick expression")
    expression = expression.strip()
    match = KEY.fullmatch(expression)
    if match:
        return (
            [(None, int(expression))]
            if expression.isdigit()
            else [(int(match[1]), int(match[2] or 0))]
        )
    if expression.startswith("max(") and expression.endswith(")"):
        body, parts, start, nesting = expression[4:-1], [], 0, 0
        for index, char in enumerate(body):
            if char == "(":
                nesting += 1
            elif char == ")":
                nesting -= 1
            elif char == "," and nesting == 0:
                parts.append(body[start:index])
                start = index + 1
            if nesting < 0:
                raise ValueError("Invalid max expression")
        parts.append(body[start:])
        if nesting:
            raise ValueError("Invalid max expression")
        terms = [term for part in parts for term in tick_terms(part, depth + 1)]
        if len(terms) > 128:
            raise ValueError("Too many max operands")
        return terms
    raise ValueError("Expected an integer, {N_TICK:n}+offset, or max(...)")


def valid_tick(expression):
    try:
        tick_terms(expression)
        return True
    except ValueError:
        return False


def shift_tick(expression, offset):
    terms = tick_terms(expression)
    if any(delta + offset < 0 for _, delta in terms):
        raise ValueError("Tick/anchor offset cannot become negative")
    values = [
        str(delta + offset) if aid is None else f"{{N_TICK:{aid}}}+{delta + offset}"
        for aid, delta in terms
    ]
    return values[0] if len(values) == 1 else "max(" + ", ".join(values) + ")"


def window_names(sample):
    replay = sample.get("replay")
    if not isinstance(replay, dict):
        return []
    names = {}
    for key in sorted(
        replay,
        key=lambda k: (
            (0, int(k)) if isinstance(k, str) and k.isdigit() else (1, str(k))
        ),
    ):
        group = replay[key]
        if isinstance(group, list):
            for event in group:
                if isinstance(event, dict) and event.get("op") in ("open", "tool-call"):
                    name = event.get("window")
                    if isinstance(name, str):
                        names.setdefault(name, None)
    # The task list is a dedicated, always-open context in task-bearing replays.
    # Do not consume a window slot for samples that never use TODO tools.
    if any(
        isinstance(group, list)
        and any(
            isinstance(event, dict)
            and event.get("op") in ("tool-call", "invisible-tool-call")
            and event.get("tool") in ("note_save", "note_list", "note_delete")
            for event in group
        )
        for group in replay.values()
    ):
        names.setdefault("notes", None)
    return list(names)


class CompileError(ValueError):
    def __init__(self, message: str, path: str = "replay"):
        super().__init__(message)
        self.path = path

    def diagnostic(self):
        return {"severity": "error", "path": self.path, "message": str(self)}


def integer(value, path, minimum=0):
    if type(value) is not int or value < minimum:
        raise CompileError(f"Expected integer >= {minimum}", path)
    return value


def weight(value, path):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise CompileError("Loss weight must be finite and nonnegative", path)
    return float(value)


@dataclass
class Encoded:
    text: str
    ids: list[int]
    offsets: list[tuple[int, int]]
    anchors: list[tuple[int, int]]  # id, count of tokens preceding boundary
    weights: list[float]


class Compiler:
    def __init__(
        self,
        tokenizer: Tokenizer,
        bos="<|bos|>",
        eos="<|eos|>",
        noop="<|noop|>",
        *,
        max_window_slots=None,
        slot_policy="uuid-hash-v1",
        tools=None,
        tool_store=None,
    ):
        if max_window_slots is not None and (
            type(max_window_slots) is not int or max_window_slots < 1
        ):
            raise ValueError("max_window_slots must be a positive integer or null")
        if slot_policy not in ("uuid-hash-v1", "declaration-v1"):
            raise ValueError("Unsupported window slot allocation policy")
        self.tools = tools or default_registry()
        self.tool_store = tool_store
        self.max_window_slots, self.slot_policy = max_window_slots, slot_policy
        self.tokenizer = tokenizer
        tokenizer.no_truncation()
        tokenizer.no_padding()
        self.special = {"bos": bos, "eos": eos, "noop": noop}
        self.ids = {k: tokenizer.token_to_id(v) for k, v in self.special.items()}
        for name, idx in self.ids.items():
            if idx is None or tokenizer.encode(
                self.special[name], add_special_tokens=False
            ).ids != [idx]:
                raise ValueError(
                    f"Tokenizer must contain {self.special[name]!r} as one dedicated token ({name})"
                )
        if len(set(self.ids.values())) != 3:
            raise ValueError("BOS, EOS and NOOP must have distinct IDs")
        self.fingerprint = hashlib.sha256(tokenizer.to_str().encode()).hexdigest()

    def slots_for(self, sample):
        names = window_names(sample)
        capacity = (
            self.max_window_slots if self.max_window_slots is not None else len(names)
        )
        if len(names) > capacity:
            raise CompileError(
                f"Trajectory uses {len(names)} window slots; project limit is {capacity}",
                "replay",
            )
        if self.slot_policy == "declaration-v1":
            return dict(zip(names, range(len(names))))
        # Partial Fisher-Yates with a versioned SHA-256 stream. It allocates only
        # O(window count) memory even when the configured slot space is large.
        seed = trajectory_uuid(sample).encode("ascii")
        swaps, result = {}, {}
        for index, name in enumerate(names):
            digest = hashlib.sha256(seed + index.to_bytes(8, "big")).digest()
            chosen = index + int.from_bytes(digest, "big") % (capacity - index)
            result[name] = swaps.get(chosen, chosen)
            swaps[chosen] = swaps.get(index, index)
        return result

    def window_header(self, slot, purpose):
        """Frame literal metadata without letting it introduce header/control tokens."""

        def field(value):
            escaped = json.dumps(value, ensure_ascii=False)[1:-1]
            return tool_text(escaped, self.special.values()).replace("<", r"\u003c")

        return f"<|cxt|>Window: {slot}\nPurpose: {field(purpose)}<|/cxt|>"

    def encode(self, event, path, noop_weight, literal=False, window_slots=None):
        if ("content" in event) == ("segments" in event):
            raise CompileError("Supply exactly one of content or segments", path)
        segments = event.get(
            "segments", [{"text": event.get("content"), "loss": event.get("loss", {})}]
        )
        if not isinstance(segments, list) or not segments:
            raise CompileError("segments must be a nonempty array", path)
        text, anchors, spans = "", [], []
        for seg in segments:
            if not isinstance(seg, dict) or not isinstance(seg.get("text"), str):
                raise CompileError("Content/segment text must be a string", path)
            if not isinstance(event.get("loss", {}), dict) or not isinstance(
                seg.get("loss", {}), dict
            ):
                raise CompileError("loss must be an object", path)
            loss = {**event.get("loss", {}), **seg.get("loss", {})}
            mask = loss.get("mask", "auto")
            if mask not in ("auto", "include", "exclude"):
                raise CompileError("Loss mask must be auto, include or exclude", path)
            w = weight(loss.get("weight", 1.0), path)
            enabled = mask == "include" or (
                mask == "auto" and event["op"] == "generate"
            )
            start = len(text)
            raw, visible, i = seg["text"], "", 0
            if literal:
                visible, i = raw, len(raw)
            while i < len(raw):
                if raw.startswith(("\\{N_TICK", "\\{WINDOW"), i):
                    visible += "{"
                    i += 2
                elif raw.startswith("{WINDOW", i):
                    match = WINDOW.match(raw, i)
                    if not match:
                        raise CompileError(
                            "Invalid window marker; expected {WINDOW:window_id}", path
                        )
                    if window_slots is None or match[1] not in window_slots:
                        raise CompileError(
                            f"Unknown window reference {match[1]!r}", path
                        )
                    visible += str(window_slots[match[1]])
                    i = match.end()
                elif raw.startswith("{N_TICK", i):
                    match = ANCHOR.match(raw, i)
                    if not match:
                        raise CompileError(
                            "Invalid marker; expected {N_TICK:nonnegative_integer}",
                            path,
                        )
                    anchors.append((int(match[1]), start + len(visible)))
                    i = match.end()
                else:
                    visible += raw[i]
                    i += 1
            text += visible
            spans.append((start, len(text), w if enabled else 0, "weight" in loss))
        enc = self.tokenizer.encode(text, add_special_tokens=False)

        def boundary(char):
            if any(a < char < b for a, b in enc.offsets):
                raise CompileError(
                    f"Marker/segment boundary at character {char} splits a tokenizer token",
                    path,
                )
            return sum(1 for a, b in enc.offsets if b <= char)

        converted = [(a, boundary(c)) for a, c in anchors]
        for a, b, _, _ in spans:
            boundary(a)
            boundary(b)
        weights = []
        for idx, (a, b) in zip(enc.ids, enc.offsets):
            matching = [s for s in spans if s[0] <= a and b <= s[1]]
            if not matching:
                raise CompileError(
                    "Tokenizer offsets do not map to content segments", path
                )
            s = matching[-1]
            w = s[2]
            if idx == self.ids["noop"] and not s[3]:
                w *= noop_weight
            weights.append(w)
        if self.ids["bos"] in enc.ids:
            raise CompileError("BOS is automatic; do not put it in content", path)
        if event["op"] == "append" and any(
            i in (self.ids["eos"], self.ids["noop"]) for i in enc.ids
        ):
            raise CompileError("EOS and NOOP are model-owned control tokens", path)
        if self.ids["eos"] in enc.ids and enc.ids[-1] != self.ids["eos"]:
            raise CompileError("No content is allowed after EOS", path)
        if enc.ids.count(self.ids["eos"]) > 1:
            raise CompileError("Only one EOS may close a window", path)
        return Encoded(text, enc.ids, enc.offsets, converted, weights)

    def show_tools(self, event, sample, path):
        selected = event.get("tools", [])
        if (
            not isinstance(selected, list)
            or any(not isinstance(name, str) for name in selected)
            or len(set(selected)) != len(selected)
        ):
            raise CompileError("show_tools tools must be unique tool names", path)
        additional = event.get("randomly-add-additional-tools", False)
        if type(additional) is not bool:
            raise CompileError("randomly-add-additional-tools must be a boolean", path)
        try:
            for name in selected:
                self.tools.check_visibility(name)
        except ToolError as error:
            raise CompileError(str(error), path) from error
        if additional:
            rng = random.Random(trajectory_uuid(sample) + path)
            candidates = sorted(
                {name for name, tool in self.tools.tools.items() if not tool.invisible}
                - set(selected)
            )
            selected = selected + [name for name in candidates if rng.random() < 0.5]
        schemas = [self.tools.get(name).definition() for name in selected]
        return {
            "op": "append",
            "window": event["window"],
            "source": "tools",
            "content": json.dumps(schemas, ensure_ascii=False, separators=(",", ":")),
            "delivery": "instant",
            "loss": {"mask": "exclude"},
        }

    def compile(self, sample: dict, *, record_dependencies=True) -> dict:
        """Compile locally; discovery can opt out of recording tool dependencies."""
        from .sandbox_replay import compile_sandbox, validate_call

        if not isinstance(sample, dict):
            raise CompileError("Sample must be an object", "sample")
        if (
            sample.get("schema-version") != "1.0"
            or sample.get("tick-policy") != "external-first"
        ):
            raise CompileError(
                "Requires schema-version 1.0 and tick-policy external-first", "sample"
            )
        if not isinstance(sample.get("id"), str) or not sample["id"]:
            raise CompileError("A nonempty sample id is required", "id")
        replay = sample.get("replay")
        if not isinstance(replay, dict) or not replay:
            raise CompileError("replay must be a nonempty map", "replay")
        defaults = sample.get("defaults", {})
        if not isinstance(defaults, dict):
            raise CompileError("defaults must be an object", "defaults")
        noop_weight = weight(defaults.get("implicit-noop-loss-weight", 0.1), "defaults")
        window_headers = defaults.get("window-headers", True)
        if type(window_headers) is not bool:
            raise CompileError(
                "window-headers must be a boolean", "defaults.window-headers"
            )
        if self.tool_store is not None:
            self.tool_store.verify_tool_dependencies(sample, self.tools)
        prepared, owners = [], {}
        # Assign slots before tokenization: reference widths can affect anchors.
        keys = sorted(
            replay,
            key=lambda k: (
                (0, int(k)) if isinstance(k, str) and k.isdigit() else (1, str(k))
            ),
        )
        notes_enabled = any(
            isinstance(group, list)
            and any(
                isinstance(event, dict)
                and event.get("op") in ("tool-call", "invisible-tool-call")
                and event.get("tool") in ("note_save", "note_list", "note_delete")
                for event in group
            )
            for group in replay.values()
        )
        if notes_enabled and any(
            isinstance(group, list)
            and any(
                isinstance(event, dict)
                and event.get("window") == "notes"
                for event in group
            )
            for group in replay.values()
        ):
            raise CompileError("The notes window is managed automatically", "replay")
        window_slots = self.slots_for(sample)
        # Stable tie-breaking independent of JSON object insertion order.
        for key in keys:
            if not valid_tick(key):
                raise CompileError(
                    "Invalid tick expression: use an integer or {N_TICK:n}+offset",
                    str(key),
                )
            if not isinstance(replay[key], list):
                raise CompileError("Tick events must be an array", key)
            for order, event in enumerate(replay[key]):
                path = f"replay[{key}][{order}]"
                if not isinstance(event, dict) or event.get("op") not in (
                    "open",
                    "append",
                    "generate",
                    "tool-call",
                    "invisible-tool-call",
                    "show_tools",
                ):
                    raise CompileError(
                        "Allowed operations: open, append, generate, tool-call, invisible-tool-call, show_tools; close via generated EOS",
                        path,
                    )
                for field in ("window-type", "request-id", "caused-by"):
                    if field in event and not isinstance(event[field], str):
                        raise CompileError(f"{field} must be a string", path)
                window = event.get("window")
                if event["op"] != "invisible-tool-call" and (
                    not isinstance(window, str) or not window or len(window) > 128
                ):
                    raise CompileError(
                        "window must be a nonempty string of at most 128 characters",
                        path,
                    )
                showing_tools = event["op"] == "show_tools"
                if showing_tools:
                    event = self.show_tools(event, sample, path)
                item = {
                    "event": event,
                    "key": key,
                    "path": path,
                    "order": len(prepared),
                    "show_tools": showing_tools,
                }
                if "replace-content" in event:
                    if (
                        event["op"] != "append"
                        or type(event["replace-content"]) is not bool
                    ):
                        raise CompileError(
                            "replace-content requires an append and a boolean", path
                        )
                    item["replace_content"] = event["replace-content"]
                if event["op"] in ("tool-call", "invisible-tool-call"):
                    validate_call(
                        event,
                        path,
                        self.tools,
                        validate_arguments=event["op"] == "invisible-tool-call",
                    )
                if event["op"] == "invisible-tool-call":
                    prepared.append(item)
                    continue
                if event["op"] != "open":
                    content_event = event
                    literal = item.get("show_tools", False)
                    if event["op"] == "tool-call":
                        content_event = {
                            **event,
                            "op": "generate",
                            "delivery": "stream",
                            "tokens-per-tick": 1,
                        }
                        if "content" not in event and "segments" not in event:
                            content_event["content"] = tool_request(
                                event["tool"], event["arguments"], self.special.values()
                            )
                            literal = True
                    if event["op"] == "append" and (
                        not isinstance(event.get("source"), str) or not event["source"]
                    ):
                        raise CompileError("append requires a source", path)
                    if event.get(
                        "delivery", "stream" if event["op"] == "generate" else "instant"
                    ) not in ("instant", "stream"):
                        raise CompileError("delivery must be instant or stream", path)
                    if content_event["op"] == "generate" and (
                        content_event.get("delivery", "stream") != "stream"
                        or content_event.get("tokens-per-tick", 1) != 1
                    ):
                        raise CompileError(
                            "Generated streams emit exactly one token per tick", path
                        )
                    integer(event.get("tokens-per-tick", 1), path, 1)
                    item["rate"] = content_event.get("tokens-per-tick", 1)
                    item["instant"] = (
                        content_event["op"] == "append"
                        and event.get("delivery", "instant") == "instant"
                    )
                    item["content_op"] = content_event["op"]
                    item["encoded"] = self.encode(
                        content_event,
                        path,
                        noop_weight,
                        literal=literal,
                        window_slots=window_slots,
                    )
                    if event["op"] == "tool-call":
                        item["request_event"] = content_event
                        if self.ids["eos"] not in item["encoded"].ids:
                            validate_call(event, path, self.tools)
                    for aid, boundary in item["encoded"].anchors:
                        if aid in owners:
                            raise CompileError(f"Duplicate anchor {aid}", path)
                        owners[aid] = (item, boundary)
                    if event["op"] == "tool-call" and "result-anchor" in event:
                        aid = integer(event["result-anchor"], path)
                        if aid in owners:
                            raise CompileError(f"Duplicate anchor {aid}", path)
                        owners[aid] = (item, None)
                prepared.append(item)
        resolving, resolved, captures = set(), {}, {}

        def capture(owner, boundary, start):
            if start is None:
                return None
            if boundary is None:
                if "result_end" not in owner:
                    return None
                tick, phase = owner["result_end"], "B"
            else:
                offset = (
                    0
                    if owner["instant"] or boundary == 0
                    else (boundary - 1) // owner["rate"]
                )
                tick = start + offset
                phase = "B" if owner["content_op"] == "append" else "D"
            return {
                "tick": tick,
                "window": owner["event"]["window"],
                "path": owner["path"],
                "phase": phase,
            }

        def solve(item):
            ident = item["order"]
            if ident in resolved:
                return resolved[ident]
            if ident in resolving:
                raise CompileError("Cyclic anchor dependency", item["path"])
            resolving.add(ident)
            key = item["key"]
            captures = []
            for aid, delta in tick_terms(key):
                if aid is None:
                    captures.append((delta, None, None, None, delta))
                    continue
                if aid not in owners:
                    raise CompileError(f"Undefined anchor {aid}", item["path"])
                owner, n = owners[aid]
                captured = capture(owner, n, solve(owner))
                if captured is None:
                    resolving.remove(ident)
                    return None
                captures.append(
                    (captured["tick"] + delta, aid, n, captured["phase"], delta)
                )
            tick = max(value[0] for value in captures)
            for at, aid, n, phase, delta in captures:
                if at != tick:
                    continue
                if delta == 0 and phase == "D" and n > 0:
                    raise CompileError(
                        f"Anchor {aid} captures a phase-D output; dependent event must start at +1 or later",
                        item["path"],
                    )
                if (
                    delta == 0
                    and (n is None or n > 0)
                    and phase == "B"
                    and item.get("content_op") != "generate"
                ):
                    raise CompileError(
                        f"Anchor {aid} is captured in phase B; open/append dependents must start at +1 or later",
                        item["path"],
                    )
            if tick >= MAX_TICKS:
                raise CompileError(
                    f"Tick limit exceeded ({MAX_TICKS:,}); valid ticks are 0..{MAX_TICKS - 1:,}. "
                    "Move events earlier or reduce anchor offsets/stream duration.", item["path"]
                )
            resolving.remove(ident)
            resolved[ident] = tick
            return tick

        def resolve_items(*, invalidate=False):
            if invalidate:
                resolved.clear()
            for item in prepared:
                item["tick"] = solve(item)

        def finish_result(item, call, tick, output):
            result = {
                "op": "append",
                "window": call["window"],
                "source": "tool",
                "delivery": item["event"].get("delivery", "instant"),
                "content": "<tool_result>"
                + tool_text(output, self.special.values(), json_encoded=True).replace(
                    "<", r"\u003c"
                )
                + "</tool_result>",
                "loss": {"mask": "exclude"},
            }
            encoded = self.encode(result, item["path"], noop_weight, literal=True)
            rate = item["event"].get("tokens-per-tick", 1)
            if result["delivery"] == "stream" and call["results"]:
                tick = max(tick, call["results"][-1]["end"] + 1)
            offset = (
                (len(encoded.ids) - 1) // rate
                if result["delivery"] == "stream" and encoded.ids
                else 0
            )
            end = tick + offset
            if end >= MAX_TICKS:
                raise CompileError(
                    f"Expanded stream exceeds tick limit ({MAX_TICKS:,}); move the result earlier or reduce its duration.",
                    item["path"] + ".result",
                )
            call["results"].append(
                {
                    "event": result,
                    "encoded": encoded,
                    "tick": tick,
                    "end": end,
                    "rate": rate,
                    "instant": result["delivery"] == "instant",
                }
            )

        if notes_enabled:
            from .tools._notes import notes_json

            initial_note_event = {
                "op": "append",
                "window": "notes",
                "source": "system",
                "content": notes_json({}),
                "loss": {"mask": "exclude"},
            }
            prepared.extend(
                [
                    {
                        "event": {"op": "open", "window": "notes", "window-type": "notes"},
                        "key": "0",
                        "tick": 0,
                        "path": "notes.open",
                        "order": -2,
                    },
                    {
                        "event": initial_note_event,
                        "key": "0",
                        "tick": 0,
                        "path": "notes.initial",
                        "order": -1,
                        "instant": True,
                        "rate": 1,
                        "encoded": self.encode(
                            initial_note_event, "notes.initial", noop_weight, literal=True
                        ),
                    },
                ]
            )
        resolve_items()
        sandbox, tool_calls, window_changes = compile_sandbox(
            prepared, sample, self, resolve_items, finish_result
        )
        resolve_items()
        if any(item["tick"] is None for item in prepared):
            raise CompileError("Cyclic anchor dependency")
        for aid, (owner, n) in owners.items():
            captures[str(aid)] = capture(owner, n, owner["tick"])
        calls_by_path = {call["path"]: call for call in tool_calls}
        expanded = []
        tool_windows = {
            call["window"] for call in tool_calls if not call.get("invisible")
        }
        explicit_opens = {
            item["event"]["window"]: item
            for item in prepared
            if item["event"]["op"] == "open"
        }
        for window in sorted(tool_windows):
            if window in explicit_opens:
                opener = explicit_opens[window]
                if opener["tick"] != 0 or opener["event"].get("window-type") != "tool":
                    raise CompileError(
                        "Tool windows must open at tick 0 with window-type tool",
                        opener["path"],
                    )
            else:
                expanded.append(
                    {
                        "event": {
                            "op": "open",
                            "window": window,
                            "window-type": "tool",
                        },
                        "key": "0",
                        "tick": 0,
                        "path": f"tool-windows[{window}].open",
                    }
                )
        for item in prepared:
            if item["event"]["op"] == "invisible-tool-call":
                continue
            if item["event"]["op"] != "tool-call":
                expanded.append(item)
                continue
            call = calls_by_path[item["path"]]
            # Requests and results share a window that has existed since tick zero.
            # Structured arguments select a registered tool; request text remains data.
            expanded.append(
                {
                    **item,
                    "event": item["request_event"],
                    "path": item["path"] + ".request",
                    "synthetic_request": True,
                }
            )
            for index, result in enumerate(call.pop("results")):
                expanded.append(
                    {
                        **item,
                        "event": result["event"],
                        "tick": result["tick"],
                        "path": item["path"]
                        + ".result"
                        + (f".{index}" if index else ""),
                        "instant": result["instant"],
                        "rate": result["rate"],
                        "encoded": result["encoded"],
                        "synthetic_window_write": True,
                        "sort_stage": 2,
                    }
                )
        for index, change in enumerate(window_changes):
            message = {
                "op": "append",
                "window": change["window"],
                "source": "tool",
                "content": tool_text(change["content"], self.special.values()),
                "loss": {"mask": "exclude"},
            }
            expanded.append(
                {
                    "event": message,
                    "key": str(change["tick"]),
                    "tick": change["tick"],
                    "path": change["path"] + f".window.{index}",
                    "instant": True,
                    "rate": 1,
                    "replace_content": change["replace"],
                    "synthetic_window_write": True,
                    "order": change["order"],
                    "sort_stage": 1,
                    "encoded": self.encode(
                        message, change["path"], noop_weight, literal=True
                    ),
                }
            )
        expanded.sort(
            key=lambda item: (item.get("order", -1), item.get("sort_stage", 0))
        )
        prepared = expanded
        for order, item in enumerate(prepared):
            item["order"] = order
        scheduled = defaultdict(list)
        events = []
        for item in prepared:
            event, start = item["event"], item["tick"]
            if event["op"] == "open":
                item["header"] = self.encode(
                    {
                        "op": "append",
                        "content": (
                            self.window_header(
                                window_slots[event["window"]],
                                event.get("window-type", "document"),
                            )
                            if window_headers
                            else ""
                        ),
                    },
                    item["path"] + ".header",
                    noop_weight,
                    literal=True,
                )
                scheduled[start].append(
                    {
                        "item": item,
                        "phase": "A",
                        "token_id": self.ids["bos"],
                        "weight": 0,
                        "durable": True,
                        "token_index": 0,
                    }
                )
                end = start
            else:
                enc = item["encoded"]
                end = start
                for n, (tid, w) in enumerate(zip(enc.ids, enc.weights)):
                    tick = start if item["instant"] else start + n // item["rate"]
                    if tick >= MAX_TICKS:
                        raise CompileError(
                            f"Expanded stream exceeds tick limit ({MAX_TICKS:,}); move the event earlier or reduce its duration.", item["path"]
                        )
                    end = tick
                    scheduled[tick].append(
                        {
                            "item": item,
                            "phase": "B" if event["op"] == "append" else "D",
                            "token_id": tid,
                            "weight": w,
                            "durable": tid != self.ids["noop"],
                            "token_index": n,
                        }
                    )
                # Empty text is still a lifecycle/ownership check, but not a token row.
                if not enc.ids:
                    scheduled[start].append(
                        {
                            "item": item,
                            "phase": "B" if event["op"] == "append" else "D",
                            "empty": True,
                        }
                    )
            events.append(
                {
                    "path": item["path"],
                    "key": item["key"],
                    "window": event["window"],
                    "op": event["op"],
                    "start": start,
                    "end": end,
                    "text": item["encoded"].text if "encoded" in item else "",
                    "delivery": event.get(
                        "delivery", "stream" if event["op"] == "generate" else "instant"
                    ),
                }
            )
        if not scheduled and not tool_calls:
            raise CompileError("Trajectory contains no executable events")
        # External activity extends inspection history without extending model NOOPs.
        visible_end = max(scheduled, default=0)
        max_tick = max([visible_end, *(c["end"] for c in tool_calls)])
        rows, windows, live, positions = [], {}, set(), defaultdict(int)

        def row(tick, phase, window, tid, w, durable, op, path, rank=0):
            idx = len(rows)
            if idx >= MAX_ROWS:
                raise CompileError(
                    f"Expanded row limit exceeded ({MAX_ROWS:,} rows); "
                    "rows include token records and implicit NOOPs for idle open windows. "
                    "Close windows when their context is no longer needed or reduce the replay horizon."
                )
            rows.append(
                {
                    "index": idx,
                    "tick": tick,
                    "phase": phase,
                    "window": window,
                    "token_id": tid,
                    "loss_mask": int(w > 0),
                    "loss_weight": w,
                    "durable": durable,
                    "position": positions[window],
                    "op": op,
                    "event": path,
                    "input_rank": rank,
                }
            )
            if durable:
                positions[window] += 1

        openers = {
            p["event"]["window"]: p for p in prepared if p["event"]["op"] == "open"
        }
        reset_items = set()
        for tick in range(visible_end + 1):
            items = sorted(
                scheduled[tick],
                key=lambda x: (
                    {"A": 0, "B": 1, "D": 2}[x["phase"]],
                    x["item"]["order"],
                    x.get("token_index", 0),
                ),
            )
            writers = defaultdict(list)
            for delivery in items:
                item, phase = delivery["item"], delivery["phase"]
                e, wid = item["event"], item["event"]["window"]
                if phase == "A":
                    if wid in windows:
                        raise CompileError(
                            f"Window {wid} was already opened; IDs cannot be reused",
                            item["path"],
                        )
                    windows[wid] = {
                        "id": wid,
                        "slot": window_slots[wid],
                        "type": e.get("window-type", "document"),
                        "open": tick,
                        "close": None,
                        "header": item["header"].text,
                    }
                    live.add(wid)
                else:
                    if wid not in live:
                        raise CompileError(
                            f"Write to unopened/closed window {wid}", item["path"]
                        )
                    # Event-list lifecycle ordering at same resolved tick.
                    opener = openers.get(wid)
                    if (
                        opener
                        and opener["tick"] == tick
                        and opener["order"] > item["order"]
                    ):
                        raise CompileError(
                            "open must precede same-tick content", item["path"]
                        )
                    writers[wid].append(delivery)
            for wid, group in writers.items():
                # Tool messages are external input in B and may arrive while the
                # model emits its next token in D (including EOS).
                distinct = {
                    x["item"]["order"]: x["item"]
                    for x in group
                    if not x["item"].get("synthetic_window_write")
                    and not (x.get("empty") and x["item"].get("synthetic_request"))
                }
                if len(distinct) > 1 and not all(
                    p["event"]["op"] == "append" and p["instant"]
                    for p in distinct.values()
                ):
                    raise CompileError(
                        f"Overlapping writers on window {wid} at tick {tick}",
                        group[-1]["item"]["path"],
                    )
            input_rank = 0
            for delivery in items:
                item = delivery["item"]
                if item.get("replace_content") and item["order"] not in reset_items:
                    reset_items.add(item["order"])
                    wid = item["event"]["window"]
                    for previous in rows:
                        if (
                            previous["window"] == wid
                            and previous["durable"]
                            and previous["op"] not in ("open", "header")
                            and "removed_at" not in previous
                        ):
                            previous["removed_at"] = {
                                "tick": tick,
                                "input_rank": input_rank,
                            }
                    positions[wid] = sum(
                        r["durable"]
                        for r in rows
                        if r["window"] == wid and r["op"] in ("open", "header")
                    )
                if delivery.get("empty"):
                    continue
                item = delivery["item"]
                wid, phase = item["event"]["window"], delivery["phase"]
                row(
                    tick,
                    phase,
                    wid,
                    delivery["token_id"],
                    delivery["weight"],
                    delivery["durable"],
                    item["event"]["op"],
                    item["path"],
                    input_rank,
                )
                input_rank += 1
                if phase == "A":
                    # Initialization is atomic: BOS then masked context, available
                    # before B/C without consuming ticks or moving content anchors.
                    for tid in item["header"].ids:
                        row(
                            tick,
                            "A",
                            wid,
                            tid,
                            0,
                            True,
                            "header",
                            item["path"] + ".header",
                            input_rank,
                        )
                        input_rank += 1
                if phase == "D" and delivery["token_id"] == self.ids["eos"]:
                    windows[wid]["close"] = tick
                    live.remove(wid)
            for wid in sorted(live):
                if wid not in writers:
                    row(
                        tick,
                        "D",
                        wid,
                        self.ids["noop"],
                        noop_weight,
                        False,
                        "noop",
                        "implicit",
                    )
        warnings = [
            {
                "severity": "warning",
                "path": w["id"],
                "message": "Window remains open at replay end; NOOP horizon ends at final scheduled tick.",
            }
            for w in windows.values()
            if w["close"] is None and w["type"] != "notes"
        ]
        for item in prepared:
            event = item["event"]
            window = event.get("window")
            purpose = windows.get(window, {}).get("type")
            if item.get("show_tools") and (window == "system" or purpose == "system"):
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "tools_in_system",
                        "path": item["path"],
                        "message": "show_tools adds tool schemas to the system prompt. Put show_tools in a dedicated tools window instead.",
                    }
                )
            if event["op"] == "generate" and (window == "chat" or purpose == "chat"):
                text = item["encoded"].text
                for token in self.special.values():
                    text = text.replace(token, "")
                if text.strip():
                    warnings.append(
                        {
                            "severity": "warning",
                            "code": "direct_chat_generation",
                            "path": item["path"],
                            "message": "Text is generated directly into chat. Send the reply with a visible chat tool call instead; generate only EOS/NOOP controls in the chat window.",
                        }
                    )
        # Use the actual expanded schemas (including randomized additions), and
        # the normal visibility predicate so closed/replaced context cannot count.
        shown = {
            item["path"]: {
                schema["function"]["name"]
                for schema in json.loads(item["event"]["content"])
            }
            for item in prepared
            if item.get("show_tools")
        }
        shown_rows = {
            row["event"]: row
            for row in rows
            if row["event"] in shown and row["durable"]
        }
        for call in tool_calls:
            if call.get("invisible"):
                continue
            query = {"tick": call["request_start"], "phase": "D"}
            if not any(
                call["tool"] in shown[path]
                and visible_to(query, row, list(windows.values()))
                for path, row in shown_rows.items()
            ):
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "tool_not_shown",
                        "path": call["path"],
                        "message": f"Tool '{call['tool']}' is used at tick {call['request_start']} without a visible show_tools listing. Add it to show_tools before the request.",
                    }
                )
        plan = {
            "sample_id": sample["id"],
            "trajectory_uuid": trajectory_uuid(sample),
            "max_window_slots": self.max_window_slots,
            "slot_policy": self.slot_policy,
            "tokenizer_sha256": self.fingerprint,
            "special_tokens": self.ids,
            "max_tick": max_tick,
            "token_max_tick": visible_end,
            "windows": list(windows.values()),
            "window_slots": window_slots,
            "anchors": captures,
            "events": events,
            "tool_calls": tool_calls,
            "sandbox": sandbox,
            "rows": rows,
            "diagnostics": warnings,
        }
        if self.tool_store is not None and record_dependencies:
            self.tool_store.record_tool_compilation(sample, self.tools, plan)
        return plan

    def frame(self, plan, tick, *, index=None, windows=None):
        from .sandbox_replay import sandbox_frame, tool_calls_frame

        integer(tick, "tick")
        if tick > plan["max_tick"]:
            raise CompileError("Tick exceeds trajectory end", "tick")
        cumulative = defaultdict(list)
        counts = defaultdict(int)
        selected = {w["id"] for w in plan["windows"]} if windows is None else set(windows)
        rows = plan["rows"] if index is None else index.through(tick, selected)
        for r in rows:
            if r["window"] not in selected:
                continue
            if (
                r["tick"] <= tick
                and r["durable"]
                and ("removed_at" not in r or tick < r["removed_at"]["tick"])
            ):
                counts[r["window"]] += 1
                if r["token_id"] not in self.ids.values():
                    cumulative[r["window"]].append(r["token_id"])
        return {
            "tick": tick,
            "sandbox": sandbox_frame(plan, tick),
            "tool_calls": tool_calls_frame(plan, tick),
            "windows": {
                w["id"]: {
                    "text": self.tokenizer.decode(
                        cumulative[w["id"]], skip_special_tokens=False
                    ),
                    "cached_tokens": counts[w["id"]],
                }
                for w in plan["windows"]
                if w["id"] in selected
            },
        }


def visible_to(query: dict, key: dict, windows: list[dict]) -> bool:
    """Exact causal attention predicate for a target query, before target commit.

    Loss masks DO NOT change this predicate. NOOP is a decision, never a KV key.
    """
    if not key["durable"]:
        return False
    removed = key.get("removed_at")
    if removed and (
        query["tick"] > removed["tick"]
        or query["tick"] == removed["tick"]
        and (
            query["phase"] == "D"
            or query["phase"] == "B"
            and query["input_rank"] >= removed["input_rank"]
        )
    ):
        return False
    life = next(w for w in windows if w["id"] == key["window"])
    if life["open"] > query["tick"] or (
        life["close"] is not None and life["close"] < query["tick"]
    ):
        return False
    if key["tick"] < query["tick"]:
        return True
    if key["tick"] > query["tick"]:
        return False
    if query["phase"] == "D":
        return key["phase"] in ("A", "B")
    if query["phase"] == "B":
        return key["phase"] == "A" or (
            key["phase"] == "B" and key["input_rank"] < query["input_rank"]
        )
    return False


def chunks(plan: dict, ticks_per_chunk=64):
    integer(ticks_per_chunk, "ticks_per_chunk", 1)
    rows = plan["rows"]
    token_end = plan.get("token_max_tick", plan["max_tick"])
    cursor = 0
    for start in range(0, token_end + 1, ticks_per_chunk):
        begin = cursor
        while cursor < len(rows) and rows[cursor]["tick"] < start + ticks_per_chunk:
            cursor += 1
        chosen = rows[begin:cursor]
        yield {
            "format": "kidra-token-chunk/1",
            "sample_id": plan["sample_id"],
            "trajectory_uuid": plan["trajectory_uuid"],
            "max_window_slots": plan["max_window_slots"],
            "slot_policy": plan["slot_policy"],
            "tokenizer_sha256": plan["tokenizer_sha256"],
            "special_tokens": plan["special_tokens"],
            "chunk_index": start // ticks_per_chunk,
            "chunk_count": token_end // ticks_per_chunk + 1,
            "sample_row_count": len(rows),
            "tick_start": start,
            "tick_end": min(start + ticks_per_chunk - 1, token_end),
            "requires_prefix_through_row": begin - 1,
            "windows": plan["windows"],
            "window_slots": plan["window_slots"],
            "rows": chosen,
            "token_ids": [r["token_id"] for r in chosen],
            "loss_mask": [r["loss_mask"] for r in chosen],
            "loss_weights": [r["loss_weight"] for r in chosen],
            "labels": [r["token_id"] if r["loss_mask"] else -100 for r in chosen],
            "attention_rule": "external-first-v1",
            "cache_policy": "durable-only; evict closed windows after phase D; full-gradients requires attached prefix graph",
        }
