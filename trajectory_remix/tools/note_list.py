"""List all TODO tasks grouped by priority."""

from ._notes import grouped_notes
from ._support import run_builtin
from .runtime import WindowProperties

name = "note_list"
description = "List TODO tasks grouped from critical to very low priority."
schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return grouped_notes(runtime.notes)


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
