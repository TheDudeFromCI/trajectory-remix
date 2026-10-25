"""Set synthetic local time. Null fields are deterministically randomized from the trajectory seed and call count; null years use 2000–2099. Timezone accepts an IANA name, UTC or ±HH:MM."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "time_set"
description = "Set synthetic local time. Null fields are deterministically randomized from the trajectory seed and call count; null years use 2000–2099. Timezone accepts an IANA name, UTC or ±HH:MM."
schema = {
    "type": "object",
    "properties": {
        "year": {"type": ["integer", "null"], "minimum": 1, "maximum": 9999},
        "month": {"type": ["integer", "null"], "minimum": 1, "maximum": 12},
        "day": {"type": ["integer", "null"], "minimum": 1, "maximum": 31},
        "hour": {"type": ["integer", "null"], "minimum": 0, "maximum": 23},
        "minute": {"type": ["integer", "null"], "minimum": 0, "maximum": 59},
        "second": {"type": ["integer", "null"], "minimum": 0, "maximum": 59},
        "timezone": {"type": ["string", "null"], "maxLength": 128},
    },
    "required": ["year", "month", "day", "hour", "minute", "second", "timezone"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    return runtime.set_time(args)


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
