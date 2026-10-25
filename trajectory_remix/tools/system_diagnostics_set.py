"""Author-only updates to simulated system properties."""

from ._simulation import SYSTEM_DEFAULTS, SYSTEM_PROPERTIES, result, failure

name = "system_diagnostics_set"
invisible = True
description = "Merge updates into simulated system diagnostics. Omitted values are retained; used RAM/VRAM cannot exceed their totals."
schema = {
    "type": "object",
    "properties": SYSTEM_PROPERTIES,
    "minProperties": 1,
    "additionalProperties": False,
}


def run(args, state, windows):
    updated = {**SYSTEM_DEFAULTS, **state.get("system", {}), **args}
    for resource in ("ram", "vram"):
        if updated[f"{resource}_used_bytes"] > updated[f"{resource}_total_bytes"]:
            return failure(
                ValueError(f"{resource} used bytes cannot exceed total bytes")
            )
    state["system"] = updated
    return result({"synthetic": True, **updated}, modified=True)
