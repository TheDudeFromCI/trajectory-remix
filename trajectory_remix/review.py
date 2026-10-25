"""Bounded review views. Source identities and content offsets survive projection."""

import copy
import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter

from .compiler import visible_to


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def deduplicate(payload):
    strings = Counter()

    def scan(value):
        if isinstance(value, str) and len(value) > 256:
            strings[value] += 1
        elif isinstance(value, dict):
            for item in value.values():
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    scan(payload)
    refs = {text: f"c{i}" for i, (text, n) in enumerate(strings.items()) if n > 1}

    def project(value):
        if isinstance(value, str) and value in refs:
            return {"text_ref": refs[value]}
        if isinstance(value, dict):
            return {k: project(v) for k, v in value.items()}
        if isinstance(value, list):
            return [project(v) for v in value]
        return value

    result = project(payload)
    if refs:
        result["content_pool"] = {uid: text for text, uid in refs.items()}
    return result


def count(tokenizer, value):
    return len(tokenizer.encode(value, add_special_tokens=False).ids)


def fragments(text, tokenizer, limit):
    """Split exact Unicode text without decoding/reformatting the source."""
    offset = 0
    while offset < len(text):
        lo, hi = 1, len(text) - offset
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count(tokenizer, text[offset : offset + mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        if count(tokenizer, text[offset : offset + lo]) > limit:
            raise ValueError("One character exceeds the review content budget")
        yield offset, text[offset : offset + lo]
        offset += lo


def select_inputs(inputs, paths):
    result = {}
    for path in paths:
        value = inputs
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                raise ValueError(f"Unknown review input: {path}")
            value = value[key]
        result[path] = value
    return result


class ReviewView:
    def __init__(self, draft, plan, config, tokenizer, tools=None):
        self.plan, self.config, self.tokenizer = plan, config, tokenizer
        self.units, self.by_id = [], {}
        self.tools = tools
        self.text_timings = {}
        self.sources = {
            f"replay[{tick}][{index}]": event
            for tick, events in draft["replay"].items()
            for index, event in enumerate(events)
        }
        self.times = {e["path"]: e for e in plan["events"]}
        self.calls = {c["path"]: c for c in plan["tool_calls"]}
        self.windows = {w["id"]: w for w in plan["windows"]}
        self.listings = {
            path: [s["function"]["name"] for s in json.loads(self.times[path]["text"])]
            for path, event in self.sources.items()
            if event["op"] == "show_tools"
        }
        self.listing_rows = {
            r["event"]: r
            for r in plan["rows"]
            if r["event"] in self.listings and r["durable"]
        }
        self.final_answer = next(
            (
                c["arguments"].get("content", "")
                for c in reversed(plan["tool_calls"])
                if c["tool"] == "chat"
                and c.get("outcome", {}).get("ok")
                and not c.get("invisible")
            ),
            "",
        )
        self.final_call_id = next(
            (
                c["path"]
                for c in reversed(plan["tool_calls"])
                if c["tool"] == "chat"
                and c.get("outcome", {}).get("ok")
                and not c.get("invisible")
            ),
            None,
        )
        records = []
        order = {
            f"replay[{key}][{i}]": n
            for n, (key, i) in enumerate(
                (key, i)
                for key in sorted(
                    draft["replay"],
                    key=lambda k: (0, int(k)) if k.isdigit() else (1, k),
                )
                for i in range(len(draft["replay"][key]))
            )
        }
        for path, event in self.sources.items():
            call, timing = self.calls.get(path), self.times.get(path, {})
            start = call["request_start"] if call else timing.get("start", 0)
            end = (
                call["end"]
                if call and call.get("invisible")
                else max(start, call["start"] - 1)
                if call
                else timing.get("end", start)
            )
            purpose = self.windows.get(event.get("window"), {}).get("type")
            base = {
                "event_id": path,
                "op": event["op"],
                "window": event.get("window"),
                "purpose": purpose,
                "tick": timing.get("key", self.tick_key(path)),
                "start": start,
                "end": end,
            }
            if call:
                base.update(tool=call["tool"], invisible=call.get("invisible", False))
            body = {
                k: v for k, v in event.items() if k not in ("window", "op", "outcome")
            }
            view = config.view
            if event["op"] == "show_tools":
                expanded = self.times.get(path, {}).get("text", "[]")
                body = {"tools": [s["function"]["name"] for s in json.loads(expanded)]}
            if view == "layout":
                body = {
                    k: v
                    for k, v in body.items()
                    if k not in ("content", "segments", "arguments")
                }
                if event["op"] == "append" and event.get("window") == "chat":
                    body["content"] = event.get("content", "")
                if call and call["tool"] == "file_write":
                    body["file_path"] = call["arguments"].get("path")
                    body["content_characters"] = len(
                        call["arguments"].get("content", "")
                    )
            elif view == "tools":
                if (
                    not call
                    and event["op"] not in ("open", "show_tools")
                    and event.get("window") != "chat"
                    and purpose != "reasoning"
                ):
                    continue
                if not call and purpose == "reasoning":
                    body = {
                        "content": event.get("content", ""),
                        **(
                            {"segments": event["segments"]}
                            if "segments" in event
                            else {}
                        ),
                    }
            elif view == "reasoning":
                if (
                    purpose != "reasoning"
                    and event.get("window") != "chat"
                    and not (call and call["tool"] == "chat")
                ):
                    continue
            elif view == "correctness":
                # The complete final answer is shared once. Review evidence in bounded groups.
                external = event["op"] == "append" and (
                    event.get("window") == "chat"
                    or purpose in ("document", "file", "attachment")
                )
                if external:
                    body = {
                        k: v
                        for k, v in body.items()
                        if k in ("content", "segments", "source")
                    }
                elif (
                    not call
                    or call["path"] == self.final_call_id
                    or (call.get("invisible") and call["tool"] != "file_write")
                ):
                    continue
                else:
                    body = {"tool": call["tool"], "arguments": call["arguments"]}
            records.append((start, path, base, body))
            if (
                call
                and not call.get("invisible")
                and view in ("tools", "correctness")
                and not (view == "correctness" and call["tool"] == "chat")
            ):
                result = {
                    **base,
                    "op": "tool_result",
                    "start": call["end"],
                    "end": call["result_end"],
                    "editable": False,
                    "complete_available_tick": call["result_end"],
                }
                records.append(
                    (
                        call["end"],
                        path + ":result",
                        result,
                        {"outcome": call["outcome"]},
                    )
                )
        for _, uid, base, body in sorted(
            records,
            key=lambda r: (
                r[0],
                0
                if r[2]["op"] == "open"
                else 1
                if r[2]["op"]
                in ("append", "show_tools", "tool_result", "invisible-tool-call")
                else 2,
                order[r[2]["event_id"]],
            ),
        ):
            parts = []

            def project(value, pointer="", uid=uid, base=base, parts=parts):
                if (
                    isinstance(value, str)
                    and count(tokenizer, value) > config.content_tokens
                ):
                    ids = []
                    for offset, text in fragments(
                        value, tokenizer, config.content_tokens
                    ):
                        part_id = f"{uid}{pointer}@{offset}"
                        ids.append(part_id)
                        parts.append(
                            {
                                **base,
                                **self.fragment_timing(
                                    base, pointer, value, offset, len(text)
                                ),
                                "id": part_id,
                                "field": pointer,
                                "offset": offset,
                                "text": text,
                            }
                        )
                    return {
                        "split_text": {
                            "field": pointer,
                            "characters": len(value),
                            "pieces": len(ids),
                        }
                    }
                if isinstance(value, dict):
                    return {
                        k: project(
                            v, pointer + "/" + k.replace("~", "~0").replace("/", "~1")
                        )
                        for k, v in value.items()
                    }
                if isinstance(value, list):
                    return [
                        project(v, pointer + "/" + str(i)) for i, v in enumerate(value)
                    ]
                return value

            self.add({**base, "id": uid, "data": project(body)})
            for part in parts:
                self.add(part)

        self.units.sort(key=lambda u: u["start"])

    def fragment_timing(self, base, pointer, value, offset, length):
        """Map source character spans to ticks using the compiled token offsets."""
        path = base["event_id"]
        source, timing = self.sources[path], self.times.get(path)
        if not timing or source["op"] not in ("append", "generate"):
            return {"timing_scope": "event_bounds"}
        if source["op"] == "append" and timing["delivery"] == "instant":
            return {"timing_scope": "instant"}
        key = (path, pointer)
        if key not in self.text_timings:
            pattern = re.compile(
                r"\\\{(?=N_TICK|WINDOW)|(?<!\\)\{N_TICK:\d+\}|(?<!\\)\{WINDOW:([^{}\r\n]+)\}"
            )

            def replacement(match):
                return (
                    "{"
                    if match[0].startswith("\\")
                    else str(self.plan["window_slots"][match[1]])
                    if match[1]
                    else ""
                )

            def transform(text):
                return pattern.sub(replacement, text)

            if pointer == "/content" and source.get("content") == value:
                prefix, full = 0, transform(value)
            elif re.fullmatch(r"/segments/\d+/text", pointer):
                index = int(pointer.split("/")[2])
                prefix = sum(
                    len(transform(seg["text"])) for seg in source["segments"][:index]
                )
                full = "".join(transform(seg["text"]) for seg in source["segments"])
            else:
                return {"timing_scope": "event_bounds"}
            if full != timing["text"]:
                return {"timing_scope": "event_bounds"}
            changes = [
                (m.start(), m.end(), len(replacement(m)))
                for m in pattern.finditer(value)
            ]
            enc = self.tokenizer.encode(full, add_special_tokens=False)
            self.text_timings[key] = (
                prefix,
                changes,
                [a for a, _ in enc.offsets],
                [b for _, b in enc.offsets],
            )
        prefix, changes, starts, ends = self.text_timings[key]

        def position(index):
            delta = 0
            for start, end, size in changes:
                if index >= end:
                    delta += size - (end - start)
                elif index > start:
                    return prefix + start + delta
                else:
                    break
            return prefix + index + delta

        first = bisect_right(ends, position(offset))
        last = max(first, bisect_left(starts, position(offset + length)) - 1)
        rate = source.get("tokens-per-tick", 1)
        return {
            "event_start": base["start"],
            "event_end": base["end"],
            "timing_scope": "token_offsets",
            "start": min(base["end"], base["start"] + first // rate),
            "end": min(base["end"], base["start"] + last // rate),
        }

    @staticmethod
    def tick_key(path):
        return path[7 : path.rfind("][")]

    def add(self, unit):
        self.units.append(unit)
        self.by_id[unit["id"]] = unit

    def boundary(self, units):
        tick = min((u["start"] for u in units), default=0)
        live = [
            {
                "id": w["id"],
                "purpose": w["type"],
                "open": w["open"],
                "close": w["close"],
            }
            for w in self.windows.values()
            if w["open"] <= tick and (w["close"] is None or w["close"] >= tick)
        ]
        history = {}
        for unit in self.units:
            if (
                unit["end"] < tick
                or unit["end"] == tick
                and unit["op"] in ("append", "tool_result", "show_tools", "open")
            ) and unit.get("window"):
                history.setdefault(unit["window"], []).append(unit["id"])
        pending = [
            {
                "event_id": c["path"],
                "tool": c["tool"],
                "start": c["start"],
                "end": c["end"],
            }
            for c in self.plan["tool_calls"]
            if c["request_start"] <= tick <= c["end"]
        ]
        return {
            "tick": tick,
            "open_windows": live,
            "pending_calls": pending,
            "visible_tools": sorted(
                {
                    tool
                    for path, row in self.listing_rows.items()
                    if visible_to(
                        {"tick": tick, "phase": "D"}, row, self.plan["windows"]
                    )
                    for tool in self.listings[path]
                }
            ),
            "history": {w: ids[-3:] for w, ids in history.items()},
            "context_access": "Request unit IDs from history, or window:<id> for earlier contents. Unseen evidence is not a verified observation.",
        }

    def payload(self, units, inputs):
        payload = {
            "view": self.config.view,
            "inputs": select_inputs(inputs, self.config.inputs),
            "boundary": self.boundary(units),
            "events": units,
        }
        if self.config.view == "correctness":
            payload["final_answer"] = self.final_answer
        if self.config.view == "tools" and self.tools:
            names = sorted(
                {u["tool"] for u in units if u.get("tool") and not u.get("invisible")}
            )
            payload["tool_schemas"] = [
                self.tools.get(name).definition() for name in names
            ]
        paths = {u["event_id"] for u in units}
        payload["diagnostics"] = [
            d for d in self.plan["diagnostics"] if d["path"] in paths
        ]
        return deduplicate(payload)

    def groups(self, inputs, fits):
        pending = self.units or [
            {"id": "empty", "event_id": "none", "start": 0, "end": 0}
        ]
        offset = 0
        while offset < len(pending):
            end = min(len(pending), offset + self.config.events_per_chunk)
            while end > offset and not fits(self.payload(pending[offset:end], inputs)):
                end -= 1
            if end == offset:
                raise ValueError(
                    "Review input budget exceeded by shared context or a single unit; reduce selected inputs/content_tokens or raise max_input_tokens"
                )
            yield self.payload(pending[offset:end], inputs)
            if end == len(pending):
                break
            offset = max(offset + 1, end - self.config.overlap)

    def context(self, requests, payload):
        known = {u["id"] for u in payload["events"]}
        extra = []
        for uid in requests:
            if uid.startswith("window:"):
                selected = [
                    u
                    for u in self.units
                    if u.get("window") == uid[7:]
                    and (
                        u["end"] < payload["boundary"]["tick"]
                        or u["end"] == payload["boundary"]["tick"]
                        and u["op"] in ("append", "tool_result", "show_tools", "open")
                    )
                ]
            else:
                if uid not in self.by_id:
                    raise ValueError(f"Unknown review context ID: {uid}")
                selected = [self.by_id[uid]]
            for unit in selected:
                if unit["id"] not in known:
                    known.add(unit["id"])
                    extra.append(unit)
        return extra


EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "enum": [
                            "replace",
                            "delete",
                            "insert_before",
                            "insert_after",
                            "append",
                            "replace_text",
                        ]
                    },
                    "event_id": {"type": "string"},
                    "event": {"type": "object"},
                    "tick": {"type": "string"},
                    "field": {"type": "string"},
                    "old": {"type": "string", "minLength": 1},
                    "new": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["edits"],
    "additionalProperties": False,
}


def apply_edits(draft, edits):
    """Apply against stable IDs in the reviewed source; never mutate on error."""
    result = copy.deepcopy(draft)
    targets = {
        f"replay[{tick}][{i}]": e
        for tick, events in result["replay"].items()
        for i, e in enumerate(events)
    }
    whole_changes, after = set(), {}
    for edit in edits:
        action = edit["action"]
        required = {
            "append": ("tick", "event"),
            "replace": ("event_id", "event"),
            "delete": ("event_id",),
            "insert_before": ("event_id", "event"),
            "insert_after": ("event_id", "event"),
            "replace_text": ("event_id", "field", "old", "new"),
        }
        if action not in required or any(key not in edit for key in required[action]):
            raise ValueError("Repair edit has an unknown action or missing fields")
        if action in ("replace", "delete"):
            if edit["event_id"] in whole_changes:
                raise ValueError(
                    "Conflicting whole-event repairs require another review"
                )
            whole_changes.add(edit["event_id"])
        if action == "append":
            result["replay"].setdefault(edit["tick"], []).append(
                copy.deepcopy(edit["event"])
            )
            continue
        target = targets.get(edit.get("event_id"))
        location = next(
            (
                (events, i)
                for events in result["replay"].values()
                for i, e in enumerate(events)
                if e is target
            ),
            None,
        )
        if location is None:
            raise ValueError("Repair refers to an unknown or deleted event")
        events, index = location
        if action == "replace_text":
            field = edit["field"]
            if not field.startswith("/"):
                raise ValueError("replace_text field must be a JSON pointer")
            keys = field[1:].split("/")
            owner = target
            for index, encoded in enumerate(keys):
                key = encoded.replace("~1", "/").replace("~0", "~")
                if isinstance(owner, list):
                    if not key.isdecimal() or int(key) >= len(owner):
                        raise ValueError("replace_text points outside the source event")
                    key = int(key)
                elif not isinstance(owner, dict) or key not in owner:
                    raise ValueError("replace_text points outside the source event")
                if index == len(keys) - 1:
                    old = owner[key]
                    if (
                        not isinstance(old, str)
                        or not edit["old"]
                        or old.count(edit["old"]) != 1
                    ):
                        raise ValueError(
                            "replace_text requires one exact, unambiguous match"
                        )
                    owner[key] = old.replace(edit["old"], edit["new"], 1)
                else:
                    owner = owner[key]
        elif action == "delete":
            events.pop(index)
        elif action == "replace":
            replacement = copy.deepcopy(edit["event"])
            events[index] = replacement
            targets[edit["event_id"]] = replacement
        else:
            inserted = copy.deepcopy(edit["event"])
            if action == "insert_after" and edit["event_id"] in after:
                previous = after[edit["event_id"]]
                index = next(i for i, item in enumerate(events) if item is previous)
            events.insert(index + (action == "insert_after"), inserted)
            if action == "insert_after":
                after[edit["event_id"]] = inserted
    result["replay"] = {
        tick: events for tick, events in result["replay"].items() if events
    }
    return result
