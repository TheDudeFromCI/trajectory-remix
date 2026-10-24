"""Read the sample's synthetic clock."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "time_now"
description = "Read the sample's synthetic clock."
schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return {"timestamp": runtime.clock.isoformat(), "synthetic": True}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
