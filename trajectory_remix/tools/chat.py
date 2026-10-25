"""Append a message from the agent to the open window named chat; fails if it is not open."""

from ._support import run_builtin
from ._protocol import ToolError
from .runtime import execution_context, WindowProperties

name = "chat"
description = "Append a message from the agent to the open window named chat; fails if it is not open."
schema = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "username": {"type": "string", "minLength": 1, "invisible": True},
    },
    "required": ["content"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    window = next((w for w in windows if w.name == "chat"), None)
    if window is None:
        raise ToolError('No window named "chat" is open')
    agent_name = args.get(
        "username", execution_context.get().metadata.get("agent_name", "Kidra")
    )
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise ToolError("metadata.agent_name must be a nonempty string")
    message = f"<|msg|>{agent_name}: {args['content']}<|/msg|>\n"
    window.content += message
    return {"window": "chat", "content": message}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
