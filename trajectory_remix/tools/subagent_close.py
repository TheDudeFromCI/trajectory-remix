"""Close a conversation and retain its history; further resumes fail."""

from copy import deepcopy
from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "subagent_close"
description = "Close a conversation and retain its history; further resumes fail."
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
    if runtime.conversations[cid]["status"] == "running":
        raise ToolError("Conversation is running")
    runtime.conversations[cid]["status"] = "closed"
    return {"conversationId": cid, **deepcopy(runtime.conversations[cid])}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
