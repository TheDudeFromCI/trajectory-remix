"""Tokenizer-aware compiler for Kidra Replay Specification 1.0.

All ids are tokenizer-native. Anchors and segment boundaries are checked against
one encoding of the entire visible content, never separately tokenized fragments.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass

from tokenizers import Tokenizer

from .tools import tool_message, tool_request, tool_text
from .dataset import trajectory_uuid

ANCHOR = re.compile(r"\{N_TICK:(\d+)\}")
WINDOW = re.compile(r"\{WINDOW:([^{}\r\n]+)\}")
KEY = re.compile(r"(?:\d+|\{N_TICK:(\d+)\}(?:\+(\d+))?)\Z")
MAX_TICKS = 100_000
MAX_ROWS = 500_000


def window_names(sample):
    replay = sample.get("replay")
    if not isinstance(replay, dict):
        return []
    names = {}
    for key in sorted(replay, key=lambda k: (0, int(k)) if isinstance(k, str) and k.isdigit() else (1, str(k))):
        group = replay[key]
        if isinstance(group, list):
            for event in group:
                if isinstance(event, dict) and event.get("op") in ("open", "tool-call"):
                    name = event.get("window")
                    if isinstance(name, str):
                        names.setdefault(name, None)
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
        self, tokenizer: Tokenizer, bos="<|bos|>", eos="<|eos|>", noop="<|noop|>",
        *, max_window_slots=None, slot_policy="uuid-hash-v1",
    ):
        if max_window_slots is not None and (type(max_window_slots) is not int or max_window_slots < 1):
            raise ValueError("max_window_slots must be a positive integer or null")
        if slot_policy not in ("uuid-hash-v1", "declaration-v1"):
            raise ValueError("Unsupported window slot allocation policy")
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
        capacity = self.max_window_slots if self.max_window_slots is not None else len(names)
        if len(names) > capacity:
            raise CompileError(f"Trajectory uses {len(names)} window slots; project limit is {capacity}", "replay")
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
                        raise CompileError(f"Unknown window reference {match[1]!r}", path)
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

    def compile(self, sample: dict) -> dict:
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
        prepared, owners = [], {}
        # Assign slots before tokenization: reference widths can affect anchors.
        keys = sorted(
            replay,
            key=lambda k: (
                (0, int(k)) if isinstance(k, str) and k.isdigit() else (1, str(k))
            ),
        )
        window_slots = self.slots_for(sample)
        # Stable tie-breaking independent of JSON object insertion order.
        for key in keys:
            if not isinstance(key, str) or not KEY.fullmatch(key):
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
                ):
                    raise CompileError(
                        "Allowed operations: open, append, generate, tool-call, invisible-tool-call; close via generated EOS",
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
                item = {
                    "event": event,
                    "key": key,
                    "path": path,
                    "order": len(prepared),
                }
                if event["op"] in ("tool-call", "invisible-tool-call"):
                    validate_call(event, path)
                if event["op"] == "invisible-tool-call":
                    prepared.append(item)
                    continue
                if event["op"] != "open":
                    content_event = event
                    literal = False
                    if event["op"] == "tool-call":
                        content_event = {
                            **event,
                            "op": (
                                "generate"
                                if event.get("delivery", "instant") == "stream"
                                else "append"
                            ),
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
                        event.get("delivery", "stream") != "stream"
                        or event.get("tokens-per-tick", 1) != 1
                    ):
                        raise CompileError(
                            "Generated streams emit exactly one token per tick", path
                        )
                    item["rate"] = integer(event.get("tokens-per-tick", 1), path, 1)
                    item["instant"] = (
                        content_event["op"] == "append"
                        and event.get("delivery", "instant") == "instant"
                    )
                    item["content_op"] = content_event["op"]
                    item["encoded"] = self.encode(
                        content_event, path, noop_weight,
                        literal=literal,
                        window_slots=window_slots,
                    )
                    if event["op"] == "tool-call":
                        item["request_event"] = content_event
                        if self.ids["eos"] in item["encoded"].ids:
                            raise CompileError(
                                "Tool request cannot close before its result", path
                            )
                    for aid, boundary in item["encoded"].anchors:
                        if aid in owners:
                            raise CompileError(f"Duplicate anchor {aid}", path)
                        owners[aid] = (item, boundary)
                prepared.append(item)
        resolving, resolved, captures = set(), {}, {}

        def solve(item):
            ident = item["order"]
            if ident in resolved:
                return resolved[ident]
            if ident in resolving:
                raise CompileError("Cyclic anchor dependency", item["path"])
            resolving.add(ident)
            key = item["key"]
            if key.isdigit():
                tick = int(key)
            else:
                m = KEY.fullmatch(key)
                aid, delta = int(m[1]), int(m[2] or 0)
                if aid not in owners:
                    raise CompileError(f"Undefined anchor {aid}", item["path"])
                owner, n = owners[aid]
                start = solve(owner)
                offset = 0 if owner["instant"] or n == 0 else (n - 1) // owner["rate"]
                captured = start + offset
                captures[str(aid)] = {
                    "tick": captured,
                    "window": owner["event"]["window"],
                    "path": owner["path"],
                    "phase": "B" if owner["content_op"] == "append" else "D",
                }
                if delta == 0 and owner["content_op"] == "generate" and n > 0:
                    raise CompileError(
                        f"Anchor {aid} captures a phase-D output; dependent event must start at +1 or later",
                        item["path"],
                    )
                if (
                    delta == 0
                    and n > 0
                    and owner["content_op"] == "append"
                    and item.get("content_op") != "generate"
                ):
                    raise CompileError(
                        f"Anchor {aid} is captured in phase B; open/append dependents must start at +1 or later",
                        item["path"],
                    )
                tick = captured + delta
            if tick >= MAX_TICKS:
                raise CompileError(f"Tick limit exceeded ({MAX_TICKS})", item["path"])
            resolving.remove(ident)
            resolved[ident] = tick
            return tick

        for item in prepared:
            item["tick"] = solve(item)
        for aid, (owner, n) in owners.items():
            offset = 0 if owner["instant"] or n == 0 else (n - 1) // owner["rate"]
            captures[str(aid)] = {
                "tick": owner["tick"] + offset,
                "window": owner["event"]["window"],
                "path": owner["path"],
                "phase": "B" if owner["content_op"] == "append" else "D",
            }
        sandbox, tool_calls = compile_sandbox(prepared, sample, self.ids["eos"])
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
                        "event": {"op": "open", "window": window, "window-type": "tool"},
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
            # Only allowlisted sandbox operations run; request text remains data.
            result = {
                "op": "append",
                "window": call["window"],
                "source": "tool",
                "content": tool_message(
                    "tool_result", call["outcome"], self.special.values()
                ),
                "loss": {"mask": "exclude"},
            }
            expanded.extend(
                [
                    {
                        **item,
                        "event": item["request_event"],
                        "path": item["path"] + ".request",
                    },
                    {
                        **item,
                        "event": result,
                        "tick": call["end"],
                        "path": item["path"] + ".result",
                        "instant": True,
                        "rate": 1,
                        "encoded": self.encode(
                            result, item["path"], noop_weight, literal=True
                        ),
                    },
                ]
            )
            if call["tool"] == "chat" and call["outcome"]["ok"]:
                message = {
                    "op": "append",
                    "window": "chat",
                    "source": "tool",
                    "content": tool_text(
                        call["outcome"]["result"]["content"], self.special.values()
                    ),
                    "loss": {"mask": "exclude"},
                }
                expanded.append(
                    {
                        **item,
                        "event": message,
                        "tick": call["end"],
                        "path": item["path"] + ".chat",
                        "instant": True,
                        "rate": 1,
                        "synthetic_chat": True,
                        "encoded": self.encode(
                            message, item["path"], noop_weight, literal=True
                        ),
                    }
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
                            ) if window_headers else ""
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
                            "Expanded stream exceeds tick limit", item["path"]
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
                raise CompileError(f"Expanded row limit exceeded ({MAX_ROWS})")
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
                # model emits its next chat token in D (including EOS).
                distinct = {
                    x["item"]["order"]: x["item"] for x in group
                    if not x["item"].get("synthetic_chat")
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
                            tick, "A", wid, tid, 0, True, "header",
                            item["path"] + ".header", input_rank,
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
            if w["close"] is None
        ]
        return {
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

    def frame(self, plan, tick):
        from .sandbox_replay import sandbox_frame

        integer(tick, "tick")
        if tick > plan["max_tick"]:
            raise CompileError("Tick exceeds trajectory end", "tick")
        cumulative = defaultdict(list)
        counts = defaultdict(int)
        for r in plan["rows"]:
            if r["tick"] <= tick and r["durable"]:
                counts[r["window"]] += 1
                if r["token_id"] not in self.ids.values():
                    cumulative[r["window"]].append(r["token_id"])
        return {
            "tick": tick,
            "sandbox": sandbox_frame(plan, tick),
            "windows": {
                w["id"]: {
                    "text": self.tokenizer.decode(
                        cumulative[w["id"]], skip_special_tokens=False
                    ),
                    "cached_tokens": counts[w["id"]],
                }
                for w in plan["windows"]
            },
        }


def visible_to(query: dict, key: dict, windows: list[dict]) -> bool:
    """Exact causal attention predicate for a target query, before target commit.

    Loss masks DO NOT change this predicate. NOOP is a decision, never a KV key.
    """
    if not key["durable"]:
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
