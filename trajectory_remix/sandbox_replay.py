"""Deterministic sandbox history. Replaying never contacts an LLM or the host."""

import copy
import json

from .compiler import MAX_TICKS, CompileError, integer
from .tools import STORES, Sandbox, ToolError, tool_failure

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
        if not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool:
            raise CompileError("Recorded outcome requires an ok boolean", path)
        if outcome["ok"]:
            result = outcome.get("result")
            if not isinstance(result, dict) or not isinstance(
                result.get("response"), str
            ):
                raise CompileError(
                    "Subagent outcome requires result.response text", path
                )
            if "conversationId" in result and (
                not isinstance(result["conversationId"], str)
                or not result["conversationId"]
                or len(result["conversationId"]) > 128
            ):
                raise CompileError("Invalid recorded conversationId", path)
        elif not isinstance(outcome.get("error"), str):
            raise CompileError("Failed outcome requires error text", path)
        if "conversationId" in outcome and (
            not isinstance(outcome["conversationId"], str)
            or not outcome["conversationId"]
            or len(outcome["conversationId"]) > 128
        ):
            raise CompileError("Invalid recorded conversationId", path)
        if len(json.dumps(outcome).encode()) > 200_000:
            raise CompileError("Recorded outcome exceeds 200,000 bytes", path)
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
        start = item["tick"]
        if event["op"] == "tool-call":
            # Tool windows are implicitly open from tick zero.
            opened = True
        elif event["op"] == "open":
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


def compile_sandbox(items, sample, eos_id):
    metadata = sample.get("metadata", {})
    clock = metadata.get("synthetic_clock", {}) if isinstance(metadata, dict) else {}
    if not isinstance(clock, dict) or set(clock) - {"seed", "seconds_per_call"}:
        raise CompileError(
            "synthetic_clock requires seed / seconds_per_call", "metadata"
        )
    try:
        sandbox = Sandbox(**clock, max_calls=MAX_TOOL_CALLS)
    except (ValueError, TypeError, OverflowError) as error:
        raise CompileError(f"Invalid synthetic clock: {error}", "metadata") from error
    initial = sandbox.snapshot()
    calls, changes = [], []
    try:
        for item in items:
            event = item["event"]
            if event["op"] not in ("tool-call", "invisible-tool-call"):
                continue
            invisible = event["op"] == "invisible-tool-call"
            start = item["tick"] + (
                len(item["encoded"].ids) if not invisible and not item["instant"] else 0
            )
            end = start + event.get("duration-ticks", 0)
            if end >= MAX_TICKS:
                raise CompileError("Tool completion exceeds tick limit", item["path"])
            calls.append(
                {
                    "path": item["path"],
                    **(
                        {"invisible": True, "source": event.get("source", "external")}
                        if invisible
                        else {"window": event["window"]}
                    ),
                    "tool": event["tool"],
                    "arguments": copy.deepcopy(event["arguments"]),
                    "request_start": item["tick"],
                    "start": start,
                    "end": end,
                    "order": item["order"],
                    **(
                        {"request-id": event["request-id"]}
                        if "request-id" in event
                        else {}
                    ),
                }
            )
        if len(calls) > MAX_TOOL_CALLS:
            raise CompileError(f"Tool event limit exceeded ({MAX_TOOL_CALLS})")
        events = {item["path"]: item["event"] for item in items}
        # Starts expose running conversations; results and mutations commit at completion.
        # Resumes must refer to a conversation already available at dispatch.
        actions = [(c["end"], c["order"], 1, c) for c in calls]
        actions += [
            (c["start"], c["order"], 0, c) for c in calls if c["tool"] == "subagent"
        ]
        pending = {}
        previous = initial
        for tick, _, phase, call in sorted(actions, key=lambda a: a[:3]):
            event = events[call["path"]]
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
            current = sandbox.snapshot()
            patch = {"clock": current["clock"], "calls": current["calls"]}
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
        state.update(clock=patch["clock"], calls=patch["calls"])
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
