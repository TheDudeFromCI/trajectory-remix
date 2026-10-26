"""Create or update a prioritized TODO task, including optional task dependencies."""

from ._notes import PRIORITIES, STATUSES
from ._protocol import ToolError
from ._support import run_builtin
from .runtime import WindowProperties

name = "note_save"
description = (
    "Create or update a prioritized TODO task. Supply name, description, status and "
    "priority when creating a task; subsequent calls may update individual fields. "
    "blocked_by contains task names, not free-form notes."
)
schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
        "description": {"type": "string", "minLength": 1, "maxLength": 5000},
        "priority": {"type": "string", "enum": list(PRIORITIES)},
        "status": {"type": "string", "enum": list(STATUSES)},
        "blocked_by": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
            "maxItems": 64,
            "uniqueItems": True,
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    task_name = args["name"]
    if not task_name.strip():
        raise ToolError("Task name cannot be blank")
    previous = runtime.notes.get(task_name)
    if previous is None and not {"description", "status", "priority"} <= args.keys():
        raise ToolError("New tasks require description, status and priority")
    note = {
        **(previous or {}),
        **{key: value for key, value in args.items() if key != "name"},
    }
    if not note["description"].strip():
        raise ToolError("Task description cannot be blank")
    blockers = note.get("blocked_by", [])
    if any(not item.strip() for item in blockers):
        raise ToolError("Blocker names cannot be blank")

    # References may be forward-declared, but cycles must never be stored.
    pending, visited = list(blockers), set()
    while pending:
        key = pending.pop()
        if key == task_name:
            raise ToolError("Task dependency cycle")
        if key not in visited:
            visited.add(key)
            pending.extend(runtime.notes.get(key, {}).get("blocked_by", []))
    if note["status"] == "done" and any(
        runtime.notes.get(dep, {}).get("status") != "done" for dep in blockers
    ):
        raise ToolError("Task has unfinished or missing blockers")
    if note["status"] != "done" and any(
        task.get("status") == "done" and task_name in task.get("blocked_by", [])
        for name, task in runtime.notes.items()
        if name != task_name
    ):
        raise ToolError("Completed tasks depend on this task")

    return runtime.put(runtime.notes, task_name, note)


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
