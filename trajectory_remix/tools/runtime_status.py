"""Read synthetic time, completed call count, task counts and subagent lifecycle states."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "runtime_status"
description = "Read synthetic time, completed call count, task counts and subagent lifecycle states."
schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return {
        "clock": runtime.clock.isoformat(),
        "synthetic": True,
        "calls": runtime.calls,
        "tasks": {
            status: sum((n["status"] == status for n in runtime.notes.values()))
            for status in ("todo", "doing", "blocked", "done")
        },
        "subagents": [runtime.conversation_summary(k) for k in runtime.conversations],
    }


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
