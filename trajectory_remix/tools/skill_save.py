"""Store a text skill in the sample sandbox."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "skill_save"
description = "Store a text skill in the sample sandbox."
schema = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
    "required": ["name", "content"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return runtime.put(runtime.skills, args["name"], args["content"])


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
