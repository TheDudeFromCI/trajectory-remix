"""Generic deterministic tick scheduling and sparse opaque state snapshots."""

import copy
import heapq
import json
from dataclasses import replace

from .compiler import MAX_TICKS, CompileError, integer
from .dataset import trajectory_uuid
from .tools import ExecutionContext, ToolError, WindowProperties, tool_text

MAX_TOOL_CALLS = 256


def validate_call(event, path, registry, *, validate_arguments=True):
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
        if validate_arguments:
            registry.validate(event["tool"], event["arguments"], invisible)
        else:
            from .tools.runtime import reject_invisible

            tool = registry.check_visibility(event["tool"], invisible)
            if not invisible:
                reject_invisible(event["arguments"], tool.schema)
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
    notes_enabled = any(
        item["event"].get("op") == "open"
        and item["event"].get("window") == "notes"
        and item["event"].get("window-type") == "notes"
        for item in items
    )
    if notes_enabled:
        from .tools._notes import notes_json

    metadata = sample.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    context = ExecutionContext(
        metadata=metadata,
        clock=metadata.get("synthetic_clock", {}),
        seed=trajectory_uuid(sample),
    )
    from .tools._clock import initialize

    try:
        initialize(state, context.clock)
    except (TypeError, ValueError) as error:
        raise CompileError(str(error), "metadata.synthetic_clock.tick_rate") from error
    initial = copy.deepcopy(state)
    unresolved = [
        item
        for item in items
        if item["event"]["op"] in ("tool-call", "invisible-tool-call")
    ]
    if len(unresolved) > MAX_TOOL_CALLS:
        raise CompileError(f"Tool event limit exceeded ({MAX_TOOL_CALLS})")
    actions, by_path = [], {item["path"]: item for item in unresolved}
    closures, last_runs = {}, {}

    def update_closures():
        closures.clear()
        for candidate in items:
            encoded = candidate.get("encoded")
            if (
                candidate["tick"] is not None
                and candidate["event"]["op"] in ("generate", "tool-call")
                and encoded is not None
                and compiler.ids["eos"] in encoded.ids
            ):
                window = candidate["event"]["window"]
                tick = (
                    candidate["tick"]
                    + encoded.ids.index(compiler.ids["eos"]) // candidate["rate"]
                )
                closures[window] = min(closures.get(window, tick), tick)

    def closing_tick(window):
        return closures.get(window)

    def trim_results(call, end):
        kept = []
        for result in call["results"]:
            if result["tick"] > end:
                continue
            if result["end"] > end:
                encoded = result["encoded"]
                count = (end - result["tick"] + 1) * result["rate"]
                result["encoded"] = replace(
                    encoded,
                    text=compiler.tokenizer.decode(
                        encoded.ids[:count], skip_special_tokens=False
                    ),
                    ids=encoded.ids[:count],
                    offsets=encoded.offsets[:count],
                    weights=encoded.weights[:count],
                )
                result["end"] = end
            kept.append(result)
        call["results"] = kept

    def refresh_schedule():
        resolve_items()
        # Closing an already-computed stream changes its final delivered token.
        # Settle those anchors before dependent calls can be dispatched.
        for _ in range(len(calls) + 1):
            update_closures()
            changed = False
            for completed in calls:
                if "end" not in completed or completed.get("invisible"):
                    continue
                close = closing_tick(completed["window"])
                if close is None or completed["result_end"] <= close:
                    continue
                trim_results(completed, close)
                completed["result_end"] = max(
                    (result["end"] for result in completed["results"]), default=close
                )
                by_path[completed["path"]]["result_end"] = completed["result_end"]
                changed = True
            if not changed:
                return
            resolve_items(invalidate=True)
        raise CompileError("Cyclic cancellation anchor dependency")

    while unresolved or actions:
        refresh_schedule()
        waiting = []
        for item in unresolved:
            if item["tick"] is None:
                waiting.append(item)
                continue
            event = item["event"]
            invisible = event["op"] == "invisible-tool-call"
            start = item["tick"] + (0 if invisible else len(item["encoded"].ids))
            close = None if invisible else closing_tick(event["window"])
            if start + event.get("duration-ticks", 0) >= MAX_TICKS and close is None:
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
            heapq.heappush(
                actions,
                (
                    min(start, close) if close is not None else start,
                    item["order"],
                    call,
                ),
            )
        unresolved = waiting
        # A late phase-B result can resolve a generated EOS on this same tick.
        # Deliver cleanup before advancing any work to the next tick.
        rescheduled = []
        for at, order, active in actions:
            if active["path"] not in last_runs:
                request = by_path[active["path"]]
                active["request_start"] = request["tick"]
                active["start"] = request["tick"] + (
                    0 if active.get("invisible") else len(request["encoded"].ids)
                )
                at = active["start"]
            rescheduled.append(
                (min(at, closures.get(active.get("window"), at)), order, active)
            )
        actions = rescheduled
        heapq.heapify(actions)
        if not actions:
            raise CompileError("Cyclic anchor dependency", unresolved[0]["path"])
        tick, order, call = heapq.heappop(actions)
        if tick >= MAX_TICKS:
            raise CompileError("Tool did not finish before tick limit", call["path"])
        item, event = by_path[call["path"]], by_path[call["path"]]["event"]

        def write(wid, before, after):
            if notes_enabled and wid == "notes":
                raise ToolError("The notes context window is managed automatically")
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
        close = None if call.get("invisible") else closing_tick(call["window"])
        cancelled = close is not None and close <= tick
        call_context = replace(
            context,
            tick=tick,
            start=call["start"],
            duration=event.get("duration-ticks", 0),
            call_id=call["path"],
            invisible=call.get("invisible", False),
            recorded_outcome=event.get("outcome"),
        )
        notes_before = copy.deepcopy(state.get("notes", {})) if notes_enabled else None
        try:
            if cancelled and (
                tick < call["start"] or last_runs.get(call["path"]) == tick
            ):
                output, done, modified = "", False, False
            else:
                output, done, modified = compiler.tools.run_tick(
                    call["tool"], call["arguments"], state, windows, call_context
                )
                last_runs[call["path"]] = tick
            if cancelled and not done:
                if tick >= call["start"]:
                    # EOS is phase D; the final normal phase-B run at this tick stands.
                    # A terminal run is cleanup only: its output is deliberately discarded.
                    _, _, cleaned = compiler.tools.run_tick(
                        call["tool"],
                        call["arguments"],
                        state,
                        windows,
                        replace(call_context, cancelled=True),
                    )
                    modified |= cleaned
                done = True
                call["cancelled"] = True
        except Exception as error:
            # An exception has no modified_state report, so its partial state is uncacheable.
            raise CompileError(
                f"Tool {call['tool']} failed: {error}", call["path"]
            ) from error
        if notes_enabled and state.get("notes", {}) != notes_before:
            # Phase-B replacement is visible to other same-tick calls after this
            # operation, and to model generation in phase D of this tick.
            writes.append(
                {
                    "tick": tick,
                    "order": order,
                    "window": "notes",
                    "path": call["path"],
                    "replace": True,
                    "content": notes_json(state.get("notes", {})),
                }
            )
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
            if call.get("cancelled"):
                call["outcome"] = {"ok": False, "error": "Tool cancelled by window EOS"}
            if call.get("invisible") and not call["outcome"]["ok"]:
                raise CompileError(
                    f"Invisible tool failed: {call['outcome'].get('error', '')}",
                    call["path"],
                )
            if not call.get("invisible"):
                if not call["outputs"] and not call.get("cancelled"):
                    finish_result(item, call, tick, "")
                if close is not None:
                    trim_results(call, close)
                item["result_end"] = max(
                    (result["end"] for result in call["results"]), default=tick
                )
                if call.get("cancelled"):
                    item["result_end"] = tick
                call["result_end"] = item["result_end"]
                if "result-anchor" in event:
                    call["result_anchor"] = event["result-anchor"]
        else:
            heapq.heappush(actions, (tick + 1, order, call))
    refresh_schedule()
    calls.sort(key=lambda c: c["order"])
    for call in calls:
        if not call.get("invisible"):
            close = closing_tick(call["window"])
            if close is not None:
                trim_results(call, close)
        call.pop("order")
    return {"initial": initial, "snapshots": snapshots}, calls, writes


def sandbox_frame(plan, tick):
    snapshots = plan["sandbox"]["snapshots"]
    key = next((key for key in reversed(snapshots) if int(key) <= tick), None)
    return (
        copy.deepcopy(snapshots[key])
        if key is not None
        else copy.deepcopy(plan["sandbox"]["initial"])
    )


def tool_calls_frame(plan, tick):
    return [
        {
            **{
                k: copy.deepcopy(v)
                for k, v in call.items()
                if k not in ("outcome", "output", "outputs", "results")
            },
            "status": "cancelled"
            if call.get("cancelled") and tick >= call["end"]
            else "composing"
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
