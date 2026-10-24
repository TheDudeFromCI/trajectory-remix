"""Read simulated system properties; never probe the host machine."""

from ._simulation import SYSTEM_DEFAULTS, result

name = "system_diagnostics"
description = "Read simulated CPU usage percent, CPU cores/model, RAM and VRAM totals/usage in bytes, GPU model, OS, and architecture. These are authored simulation values, not host measurements."
schema = {"type": "object", "properties": {}, "additionalProperties": False}


def run(args, state, windows):
    return result({"synthetic": True, **SYSTEM_DEFAULTS, **state.get("system", {})})
