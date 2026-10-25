"""Delete a task/note."""

from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "note_delete"
description = "Delete a task/note."
schema = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    if args["key"] not in runtime.notes:
        raise ToolError("Note not found")
    del runtime.notes[args["key"]]
    return {"deleted": args["key"]}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
