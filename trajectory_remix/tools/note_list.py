"""List tasks and notes."""

from copy import deepcopy
from ._support import run_builtin
from .runtime import WindowProperties

name = "note_list"
description = "List tasks and notes."
schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return deepcopy(runtime.notes)


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
