"""List retained subagent identities and lifecycle states."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "subagent_list"
description = "List retained subagent identities and lifecycle states."
schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return [runtime.conversation_summary(k) for k in runtime.conversations]


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
