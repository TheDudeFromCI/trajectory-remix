"""Deterministic sandbox history. Replaying never contacts an LLM or the host."""

import copy
import json
import heapq

from .compiler import MAX_TICKS, CompileError, integer
from .dataset import trajectory_uuid
from .tools import STORES, Sandbox, ToolError, tool_failure, validate_subagent_outcome

MAX_TOOL_CALLS = 256
EXTERNAL_TOOLS = {
    "file_write",
    "file_patch",
    "file_delete",
    "memory_save",
    "note_save",
    "note_delete",
    "skill_save",
    "time_advance",
    "time_set",
}


def validate_call(event, path):
    if event["op"] == "invisible-tool-call":
        if set(event) - {"op", "tool", "arguments", "duration-ticks", "source"}:
            raise CompileError(
                "Invisible calls accept only tool, arguments, duration-ticks and source; no window or model content",
                path,
            )
        if (
            not isinstance(event.get("tool"), str)
            or event["tool"] not in EXTERNAL_TOOLS
        ):
            raise CompileError(
                "Invisible calls require an allowlisted sandbox mutation", path
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
    if "outcome" in event:
        outcome = event["outcome"]
        if event["tool"] != "subagent":
            raise CompileError("Only subagent accepts a recorded outcome", path)
        try:
            validate_subagent_outcome(outcome)
        except ToolError as error:
            raise CompileError(str(error), path) from error
    elif event["tool"] == "subagent":
        raise CompileError("Subagent replay requires a recorded outcome", path)


def chat_context_at(items, tick, order, eos_id, metadata):
    """Chat writes land in phase B, before any same-tick generated EOS."""
    opened = False
    closed = False
    for item in items:
        event = item["event"]
        if event.get("window") != "chat":
            continue
        if event["op"] == "tool-call":
            # Implicit tool windows exist even before their schedule resolves.
            opened = True
            continue
        start = item["tick"]
        if start is None:
            continue
        if event["op"] == "open":
            if start < tick or (start == tick and item["order"] < order):
                opened = True
        elif event["op"] == "generate" and eos_id in item["encoded"].ids:
            end = start + len(item["encoded"].ids) - 1
            if end < tick:
                closed = True
    return {
        "chat_open": opened and not closed,
        "agent_name": (
            metadata.get("agent_name", "Kidra") if isinstance(metadata, dict) else "Kidra"
        ),
    }


def compile_sandbox(items, sample, eos_id, resolve_items, finish_result):
    metadata = sample.get("metadata", {})
    clock = metadata.get("synthetic_clock", {}) if isinstance(metadata, dict) else {}
    if not isinstance(clock, dict) or set(clock) - {"seed", "seconds_per_call"}:
        raise CompileError(
            "synthetic_clock requires seed / seconds_per_call", "metadata"
        )
    try:
        sandbox = Sandbox(
            **clock, random_seed=trajectory_uuid(sample), max_calls=MAX_TOOL_CALLS
        )
    except (ValueError, TypeError, OverflowError) as error:
        raise CompileError(f"Invalid synthetic clock: {error}", "metadata") from error
    initial = sandbox.snapshot()
    calls, changes = [], []
    try:
        unresolved = [item for item in items if item["event"]["op"] in (
            "tool-call", "invisible-tool-call"
        )]
        if len(unresolved) > MAX_TOOL_CALLS:
            raise CompileError(f"Tool event limit exceeded ({MAX_TOOL_CALLS})")
        by_path = {item["path"]: item for item in unresolved}
        actions, pending = [], {}
        previous = initial
        # Resolve new calls after each action: result-dependent calls can dispatch
        # before other scheduled completions. Subagent starts still expose running
        # conversations and check resume identities at dispatch.
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
                end = start + event.get("duration-ticks", 0)
                if end >= MAX_TICKS:
                    raise CompileError("Tool completion exceeds tick limit", item["path"])
                call = {
                    "path": item["path"],
                    **(
                        {"invisible": True, "source": event.get("source", "external")}
                        if invisible else {"window": event["window"]}
                    ),
                    "tool": event["tool"],
                    "arguments": copy.deepcopy(event["arguments"]),
                    "request_start": item["tick"],
                    "start": start,
                    "end": end,
                    "order": item["order"],
                    **({"request-id": event["request-id"]} if "request-id" in event else {}),
                }
                calls.append(call)
                heapq.heappush(actions, (end, call["order"], 1, call))
                if call["tool"] == "subagent":
                    heapq.heappush(actions, (start, call["order"], 0, call))
            unresolved = waiting
            if not actions:
                raise CompileError("Cyclic anchor dependency", unresolved[0]["path"])
            tick, _, phase, call = heapq.heappop(actions)
            item = by_path[call["path"]]
            event = item["event"]
            try:
                if call["tool"] == "subagent":
                    if phase == 0:
                        result = event["outcome"].get("result")
                        recorded_id = (
                            result.get(
                                "conversationId", event["outcome"].get("conversationId")
                            )
                            if isinstance(result, dict)
                            else event["outcome"].get("conversationId")
                        )
                        pending[call["path"]] = sandbox.begin_subagent(
                            call["arguments"], recorded_id
                        )
                    else:
                        sandbox.start_call(call["tool"], call["arguments"])
                        cid = pending[call["path"]]
                        if isinstance(cid, ToolError):
                            raise cid
                        outcome = copy.deepcopy(event["outcome"])
                        result = sandbox.finish_subagent(cid, outcome)
                        if outcome["ok"]:
                            outcome["result"] = result
                else:
                    context = (
                        chat_context_at(
                            items, call["end"], call["order"], eos_id, metadata
                        )
                        if call["tool"] == "chat"
                        else {}
                    )
                    outcome = {
                        "ok": True,
                        "result": None,
                    }
                    if call.get("invisible"):
                        sandbox.check_args(call["tool"], call["arguments"])
                        outcome["result"] = sandbox.execute_sync(
                            call["tool"], call["arguments"]
                        )
                    else:
                        outcome["result"] = sandbox.call_sync(
                            call["tool"], call["arguments"], **context
                        )
            except (ToolError, ValueError, OverflowError) as error:
                if phase == 0:
                    pending[call["path"]] = ToolError(str(error))
                elif call["tool"] == "subagent" and isinstance(
                    pending.get(call["path"]), str
                ):
                    cid = pending[call["path"]]
                    sandbox.conversations[cid]["status"] = "failed"
                    error = ToolError(str(error), cid)
                if call.get("invisible"):
                    # A hidden failure has no model observation; diagnose it for the author.
                    raise CompileError(
                        f"Invisible tool failed: {error}", call["path"]
                    ) from error
                outcome = tool_failure(error)
            if phase == 1:
                call["outcome"] = outcome
                if not call.get("invisible"):
                    finish_result(item, call)
            current = sandbox.snapshot()
            patch = {
                "clock": current["clock"],
                "clock_timezone": current["clock_timezone"],
                "calls": current["calls"],
            }
            for name in STORES:
                before, after = previous[name], current[name]
                patch[name] = {
                    "set": {
                        k: v
                        for k, v in after.items()
                        if k not in before or v != before[k]
                    },
                    "delete": [k for k in before if k not in after],
                }
            changes.append({"tick": tick, "patch": patch})
            previous = current
        calls.sort(key=lambda call: call["order"])
        for call in calls:
            del call["order"]
        return {"initial": initial, "changes": changes}, calls
    finally:
        sandbox.close()


def sandbox_frame(plan, tick):
    history = plan["sandbox"]
    state = copy.deepcopy(history["initial"])
    for change in history["changes"]:
        if change["tick"] > tick:
            break
        patch = change["patch"]
        state.update(
            clock=patch["clock"], clock_timezone=patch.get("clock_timezone"),
            calls=patch["calls"],
        )
        for name in STORES:
            for key in patch[name]["delete"]:
                state[name].pop(key, None)
            state[name].update(copy.deepcopy(patch[name]["set"]))
    state["tool_calls"] = [
        {
            **{k: copy.deepcopy(v) for k, v in call.items() if k != "outcome"},
            "status": (
                "composing"
                if tick < call["start"]
                else "running"
                if tick < call["end"]
                else "completed" if call["outcome"]["ok"] else "failed"
            ),
            **(
                {"outcome": copy.deepcopy(call["outcome"])}
                if tick >= call["end"]
                else {}
            ),
        }
        for call in plan["tool_calls"]
        if call["request_start"] <= tick
    ]
    return state
