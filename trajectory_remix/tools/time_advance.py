"""Advance synthetic time without real sleeping."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "time_advance"
invisible = True
description = "Advance synthetic time without real sleeping."
schema = {
    "type": "object",
    "properties": {"seconds": {"type": "number", "minimum": 0}},
    "required": ["seconds"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    runtime.advance_clock(args["seconds"])
    return {"timestamp": runtime.clock.isoformat(), "synthetic": True}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
