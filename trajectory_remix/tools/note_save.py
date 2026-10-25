"""Create/update a task with optional owner, dependencies, blockers and acceptance criteria. Omitted optional fields are retained."""

from copy import deepcopy
from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "note_save"
description = "Create/update a task with optional owner, dependencies, blockers and acceptance criteria. Omitted optional fields are retained."
schema = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "text": {"type": "string"},
        "status": {"type": "string", "enum": ["todo", "doing", "blocked", "done"]},
        "owner": {"type": "string"},
        "depends_on": {"type": "array", "items": {"type": "string"}, "maxItems": 64},
        "blockers": {"type": "array", "items": {"type": "string"}, "maxItems": 64},
        "acceptance_criteria": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 64,
        },
    },
    "required": ["key", "text", "status"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    note = {
        **runtime.notes.get(args["key"], {}),
        **{k: deepcopy(v) for k, v in args.items() if k != "key"},
    }
    dependencies = note.get("depends_on", [])
    if args["key"] in dependencies:
        raise ToolError("A task cannot depend on itself")
    pending, visited = (list(dependencies), set())
    while pending:
        key = pending.pop()
        if key == args["key"]:
            raise ToolError("Task dependency cycle")
        if key not in visited:
            visited.add(key)
            pending.extend(runtime.notes.get(key, {}).get("depends_on", []))
    if note["status"] == "done" and (
        note.get("blockers")
        or any(
            (runtime.notes.get(dep, {}).get("status") != "done" for dep in dependencies)
        )
    ):
        raise ToolError("Task has blockers or unfinished dependencies")
    return runtime.put(runtime.notes, args["key"], note)


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
