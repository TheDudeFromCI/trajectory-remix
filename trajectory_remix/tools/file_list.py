"""List all virtual files."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "file_list"
description = "List all virtual files."
schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return sorted(runtime.files)


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
