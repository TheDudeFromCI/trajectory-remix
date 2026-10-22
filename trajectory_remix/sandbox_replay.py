"""Deterministic sandbox history. Replaying never contacts an LLM or the host."""

import copy
import json

from .compiler import MAX_TICKS, CompileError, integer
from .tools import Sandbox, ToolError

STORES = ("files", "memory", "notes", "skills")
MAX_TOOL_CALLS = 256


def validate_call(event, path):
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
        elif not isinstance(outcome.get("error"), str):
            raise CompileError("Failed outcome requires error text", path)
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
        if event["window"] != "chat":
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
            if event["op"] != "tool-call":
                continue
            start = item["tick"] + (
                len(item["encoded"].ids) if not item["instant"] else 0
            )
            end = start + event.get("duration-ticks", 0)
            if end >= MAX_TICKS:
                raise CompileError("Tool completion exceeds tick limit", item["path"])
            calls.append(
                {
                    "path": item["path"],
                    "window": event["window"],
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
        # Calls are atomic at completion. Equal completions follow replay list order.
        previous = initial
        for call in sorted(calls, key=lambda c: (c["end"], c["order"])):
            event = events[call["path"]]
            try:
                if call["tool"] == "subagent":
                    sandbox.start_call(call["tool"], call["arguments"])
                    outcome = copy.deepcopy(event["outcome"])
                else:
                    context = (
                        chat_context_at(
                            items, call["end"], call["order"], eos_id, metadata
                        )
                        if call["tool"] == "chat" else {}
                    )
                    outcome = {
                        "ok": True,
                        "result": sandbox.call_sync(
                            call["tool"], call["arguments"], **context
                        ),
                    }
            except (ToolError, ValueError, OverflowError) as error:
                outcome = {"ok": False, "error": str(error)}
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
            changes.append({"tick": call["end"], "patch": patch})
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
