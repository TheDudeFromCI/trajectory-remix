"""Delete a TODO task."""

from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "note_delete"
description = "Delete a TODO task by name."
schema = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 128}},
    "required": ["name"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    task_name = args["name"]
    if task_name not in runtime.notes:
        raise ToolError("Task not found")
    if any(
        note.get("status") == "done" and task_name in note.get("blocked_by", [])
        for name, note in runtime.notes.items()
        if name != task_name
    ):
        raise ToolError("Completed tasks depend on this task")
    del runtime.notes[task_name]
    return {"deleted": task_name}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
