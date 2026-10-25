"""Authored synthetic conversations, using the same tick protocol as every tool."""

import json
from ._support import load_state
from ._protocol import ToolError, tool_failure, validate_subagent_outcome
from .runtime import execution_context

name = "subagent"
description = (
    "Simulate a conversation using an authored reply. Resume by conversationId."
)
schema = {
    "type": "object",
    "properties": {
        "task": {"type": "string"},
        "resume": {"type": "string"},
        "artifacts": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["path", "version"],
                "additionalProperties": False,
            },
        },
        "response": {"type": "string", "invisible": True},
    },
    "required": ["task"],
    "additionalProperties": False,
}


def run(args, state, windows):
    context = execution_context.get()
    if context.cancelled:
        pending = state.get("subagent_runs", {})
        cid = pending.pop(context.call_id, None)
        if not pending:
            state.pop("subagent_runs", None)
        if cid is not None:
            state["conversations"][cid]["status"] = "cancelled"
        return "", True, cid is not None
    if "response" in args:
        state.setdefault("subagent_replies", {})[args["task"]] = {
            "ok": True,
            "result": {"response": args["response"]},
        }
        return "Reply recorded", True, True
    outcome = context.recorded_outcome or state.get("subagent_replies", {}).get(
        args["task"]
    )
    runtime = load_state(state)
    modified = False
    try:
        if outcome is None:
            raise ToolError(
                "Subagent requires an authored response (invisible response argument or recorded outcome)"
            )
        validate_subagent_outcome(outcome)
        pending = state.get("subagent_runs", {})
        if context.call_id not in pending:
            result = outcome.get("result", {})
            cid = runtime.begin_subagent(
                args, result.get("conversationId", outcome.get("conversationId"))
            )
            pending = state.setdefault("subagent_runs", {})
            pending[context.call_id] = cid
            state.update(runtime.snapshot())
            modified = True
        if context.elapsed < context.duration:
            return "", False, modified
        cid = pending.pop(context.call_id)
        modified = True
        if not context.invisible:
            runtime.calls += 1
        result = runtime.finish_subagent(cid, outcome)
        state.update(runtime.snapshot())
        modified = True
        if not outcome["ok"]:
            raise ToolError(outcome["error"], cid)
        return json.dumps({"ok": True, "result": result}), True, modified
    except (ToolError, ValueError, OverflowError) as error:
        return json.dumps(tool_failure(error)), True, modified
