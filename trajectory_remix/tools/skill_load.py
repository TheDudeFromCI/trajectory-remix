"""Load a sample sandbox skill."""

from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "skill_load"
description = "Load a sample sandbox skill."
schema = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    if args["name"] not in runtime.skills:
        raise ToolError("Skill not found")
    return {"name": args["name"], "content": runtime.skills[args["name"]]}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
