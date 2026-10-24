"""Generic deterministic tick scheduling and sparse opaque state snapshots."""

import copy
import heapq
import json
from dataclasses import replace

from .compiler import MAX_TICKS, CompileError, integer
from .dataset import trajectory_uuid
from .tools import ExecutionContext, ToolError, WindowProperties, tool_text

MAX_TOOL_CALLS = 256


def validate_call(event, path, registry):
    invisible = event["op"] == "invisible-tool-call"
    if invisible and set(event) - {
        "op",
        "tool",
        "arguments",
        "duration-ticks",
        "source",
    }:
        raise CompileError(
            "Invisible calls accept only tool, arguments, duration-ticks and source; no window or model content",
            path,
        )
    if "source" in event and (
        not isinstance(event["source"], str) or not event["source"]
    ):
        raise CompileError("Invisible source must be a nonempty string", path)
    if not isinstance(event.get("tool"), str) or not event["tool"]:
        raise CompileError("tool-call requires a tool name", path)
    if not isinstance(event.get("arguments"), dict):
        raise CompileError("tool-call arguments must be an object", path)
    integer(event.get("duration-ticks", 0), path)
    try:
        registry.validate(event["tool"], event["arguments"], invisible)
    except ToolError as error:
        raise CompileError(str(error), path) from error


class WindowList(list):
    """Build window text only when a tool reads it; waiting tools pay no decoding cost."""

    def __init__(self, factory):
        super().__init__()
        self._factory = factory

    def _load(self):
        if self._factory is not None:
            factory, self._factory = self._factory, None
            super().extend(factory())

    def __iter__(self):
        self._load()
        return super().__iter__()

    def __len__(self):
        self._load()
        return super().__len__()

    def __getitem__(self, key):
        self._load()
        return super().__getitem__(key)

    def __contains__(self, value):
        self._load()
        return super().__contains__(value)

    def __repr__(self):
        self._load()
        return super().__repr__()

    def __eq__(self, other):
        self._load()
        if isinstance(other, WindowList):
            other._load()
        return super().__eq__(other)

    def __ne__(self, other):
        return not self == other

    def copy(self):
        self._load()
        return super().copy()

    def count(self, value):
        self._load()
        return super().count(value)

    def index(self, value, *args):
        self._load()
        return super().index(value, *args)


def window_view(items, calls, writes, tick, order, compiler):
    """Expose phase-B window content; same-tick model output is still unavailable."""
    opened, closed, actions = {}, set(), []
    for item in items:
        event, start = item["event"], item["tick"]
        op, wid = event["op"], event.get("window")
        if op == "tool-call":
            opened.setdefault(wid, "tool")
        if start is None or start > tick or op == "invisible-tool-call":
            continue
        if op == "open":
            opened[wid] = event.get("window-type", "document")
            continue
        enc = item.get("encoded")
        if enc is None:
            continue
        generated = op in ("generate", "tool-call")
        limit = tick - int(generated)
        if start > limit:
            continue
        count = (
            len(enc.ids)
            if item["instant"]
            else min(len(enc.ids), (limit - start + 1) * item["rate"])
        )
        if generated and compiler.ids["eos"] in enc.ids[:count]:
            closed.add(wid)
        if not enc.ids and event.get("replace-content"):
            actions.append((start, 1, item["order"], 0, 0, wid, True, []))
        for index in range(count):
            if enc.ids[index] in compiler.ids.values():
                continue
            at = start if item["instant"] else start + index // item["rate"]
            # Same-tick inputs later in source order have not arrived yet.
            if not generated and at == tick and item["order"] > order:
                continue
            actions.append(
                (
                    at,
                    2 if generated else 1,
                    item["order"],
                    0,
                    index,
                    wid,
                    event.get("replace-content", False) and index == 0,
                    [enc.ids[index]],
                )
            )
    for call in calls:
        if call.get("invisible"):
            continue
        for result in call.get("results", []):
            start, enc, instant, rate = (
                result["tick"],
                result["encoded"],
                result["instant"],
                result["rate"],
            )
            count = (
                len(enc.ids)
                if instant and start <= tick
                else max(0, min(len(enc.ids), (tick - start + 1) * rate))
            )
            for index in range(count):
                at = start if instant else start + index // rate
                if at == tick and call["order"] >= order:
                    continue
                actions.append(
                    (
                        at,
                        1,
                        call["order"],
                        2,
                        index,
                        call["window"],
                        False,
                        [enc.ids[index]],
                    )
                )
    for index, write in enumerate(writes):
        if write["tick"] <= tick:
            actions.append(
                (
                    write["tick"],
                    1,
                    write["order"],
                    1,
                    index,
                    write["window"],
                    write["replace"],
                    compiler.tokenizer.encode(
                        tool_text(write["content"], compiler.special.values()),
                        add_special_tokens=False,
                    ).ids,
                )
            )
    content = {wid: [] for wid in opened if wid not in closed}
    for _, _, _, _, _, wid, reset, text in sorted(actions):
        if wid in content:
            if reset:
                content[wid] = list(text)
            else:
                content[wid].extend(text)
    return opened, {
        wid: compiler.tokenizer.decode(tokens, skip_special_tokens=False)
        for wid, tokens in content.items()
    }


def compile_sandbox(items, sample, compiler, resolve_items, finish_result):
    state, snapshots, calls, writes = {}, {}, [], []
    metadata = sample.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    context = ExecutionContext(
        metadata=metadata,
        clock=metadata.get("synthetic_clock", {}),
        seed=trajectory_uuid(sample),
    )
    unresolved = [
        item
        for item in items
        if item["event"]["op"] in ("tool-call", "invisible-tool-call")
    ]
    if len(unresolved) > MAX_TOOL_CALLS:
        raise CompileError(f"Tool event limit exceeded ({MAX_TOOL_CALLS})")
    actions, by_path = [], {item["path"]: item for item in unresolved}
    while unresolved or actions:
        resolve_items()
        waiting = []
        for item in unresolved:
            if item["tick"] is None:
                waiting.append(item)
                continue
            event = item["event"]
            invisible = event["op"] == "invisible-tool-call"
            start = item["tick"] + (0 if invisible else len(item["encoded"].ids))
            if start + event.get("duration-ticks", 0) >= MAX_TICKS:
                raise CompileError("Tool completion exceeds tick limit", item["path"])
            call = {
                "path": item["path"],
                "tool": event["tool"],
                "arguments": copy.deepcopy(event["arguments"]),
                "request_start": item["tick"],
                "start": start,
                "order": item["order"],
                "outputs": [],
                "results": [],
                **(
                    {"invisible": True, "source": event.get("source", "external")}
                    if invisible
                    else {"window": event["window"]}
                ),
                **(
                    {"request-id": event["request-id"]} if "request-id" in event else {}
                ),
            }
            calls.append(call)
            heapq.heappush(actions, (start, item["order"], call))
        unresolved = waiting
        if not actions:
            raise CompileError("Cyclic anchor dependency", unresolved[0]["path"])
        tick, order, call = heapq.heappop(actions)
        if tick >= MAX_TICKS:
            raise CompileError("Tool did not finish before tick limit", call["path"])
        item, event = by_path[call["path"]], by_path[call["path"]]["event"]

        def write(wid, before, after):
            if before == after:
                return
            append = after.startswith(before)
            writes.append(
                {
                    "tick": tick,
                    "order": order,
                    "window": wid,
                    "path": call["path"],
                    "replace": not append,
                    "content": after[len(before) :] if append else after,
                }
            )

        def open_windows():
            opened, contents = window_view(items, calls, writes, tick, order, compiler)
            return [
                WindowProperties(wid, opened[wid], text, write)
                for wid, text in contents.items()
            ]

        windows = WindowList(open_windows)
        try:
            output, done, modified = compiler.tools.run_tick(
                call["tool"],
                call["arguments"],
                state,
                windows,
                replace(
                    context,
                    tick=tick,
                    start=call["start"],
                    duration=event.get("duration-ticks", 0),
                    call_id=call["path"],
                    invisible=call.get("invisible", False),
                    recorded_outcome=event.get("outcome"),
                ),
            )
        except Exception as error:
            # An exception has no modified_state report, so its partial state is uncacheable.
            raise CompileError(
                f"Tool {call['tool']} failed: {error}", call["path"]
            ) from error
        if modified:
            try:
                snapshots[str(tick)] = copy.deepcopy(state)
            except Exception as error:
                raise CompileError(
                    f"Could not cache tool state: {error}", call["path"]
                ) from error
        if output:
            call["outputs"].append({"tick": tick, "output": output})
            if not call.get("invisible"):
                finish_result(item, call, tick, output)
        if done:
            call["end"] = tick
            text = "".join(update["output"] for update in call["outputs"])
            try:
                outcome = json.loads(text)
            except ValueError:
                outcome = None
            call["output"] = text
            call["outcome"] = (
                outcome
                if isinstance(outcome, dict) and type(outcome.get("ok")) is bool
                else {"ok": True, "result": text}
            )
            if call.get("invisible") and not call["outcome"]["ok"]:
                raise CompileError(
                    f"Invisible tool failed: {call['outcome'].get('error', '')}",
                    call["path"],
                )
            if not call.get("invisible"):
                if not call["outputs"]:
                    finish_result(item, call, tick, "")
                item["result_end"] = max(result["end"] for result in call["results"])
                call["result_end"] = item["result_end"]
                if "result-anchor" in event:
                    call["result_anchor"] = event["result-anchor"]
        else:
            heapq.heappush(actions, (tick + 1, order, call))
    calls.sort(key=lambda c: c["order"])
    for call in calls:
        call.pop("order")
    return {"initial": {}, "snapshots": snapshots}, calls, writes


def sandbox_frame(plan, tick):
    snapshots = plan["sandbox"]["snapshots"]
    key = next((key for key in reversed(snapshots) if int(key) <= tick), None)
    return copy.deepcopy(snapshots[key]) if key is not None else {}


def tool_calls_frame(plan, tick):
    return [
        {
            **{
                k: copy.deepcopy(v)
                for k, v in call.items()
                if k not in ("outcome", "output", "outputs", "results")
            },
            "status": "composing"
            if tick < call["start"]
            else "running"
            if tick < call["end"]
            else "completed"
            if call["outcome"]["ok"]
            else "failed",
            "outputs": [
                copy.deepcopy(update)
                for update in call["outputs"]
                if update["tick"] <= tick
            ],
            **(
                {"outcome": copy.deepcopy(call["outcome"])}
                if tick >= call["end"]
                else {}
            ),
        }
        for call in plan["tool_calls"]
        if call["request_start"] <= tick
    ]
