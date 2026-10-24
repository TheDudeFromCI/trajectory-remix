"""Save a searchable memory record."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "memory_save"
description = "Save a searchable memory record."
schema = {
    "type": "object",
    "properties": {"key": {"type": "string"}, "text": {"type": "string"}},
    "required": ["key", "text"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return runtime.put(runtime.memory, args["key"], args["text"])


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
