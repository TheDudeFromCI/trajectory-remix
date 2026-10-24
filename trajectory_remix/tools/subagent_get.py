"""Inspect a retained subagent conversation."""

from copy import deepcopy
from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "subagent_get"
description = "Inspect a retained subagent conversation."
schema = {
    "type": "object",
    "properties": {"conversationId": {"type": "string"}},
    "required": ["conversationId"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    cid = args["conversationId"]
    if cid not in runtime.conversations:
        raise ToolError("Unknown conversationId")
    return {"conversationId": cid, **deepcopy(runtime.conversations[cid])}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
