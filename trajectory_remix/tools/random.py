"""Reproducible pseudo-random values from trajectory identity and stored draw count."""

import hashlib
import json
import random as python_random
from .runtime import execution_context
from ._simulation import result, failure

name = "random"
description = "Draw deterministic pseudo-random int (inclusive min/max, defaults 0/100), bool, float (min/max, defaults 0/1), or choice from choices. Recompilation reproduces the sequence."
schema = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["int", "bool", "float", "choice"]},
        "min": {"type": "number"},
        "max": {"type": "number"},
        "choices": {"type": "array", "minItems": 1, "maxItems": 1024},
    },
    "required": ["kind"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"kind": {"const": "choice"}}},
            "then": {"required": ["choices"]},
        },
        {
            "if": {"properties": {"kind": {"const": "int"}}},
            "then": {
                "properties": {"min": {"type": "integer"}, "max": {"type": "integer"}}
            },
        },
    ],
}


def run(args, state, windows):
    try:
        kind = args["kind"]
        low, high = args.get("min", 0), args.get("max", 100 if kind == "int" else 1)
        if kind in ("int", "float") and low > high:
            raise ValueError("min must be less than or equal to max")
        if kind == "int" and max(abs(low), abs(high)) > 2**53 - 1:
            raise ValueError("Integer bounds must be within ±(2**53-1)")
        counter = state.get("random", {}).get("counter", 0)
        seed = json.dumps(["random-v1", execution_context.get().seed, counter])
        rng = python_random.Random(hashlib.sha256(seed.encode()).digest())
        if kind == "int":
            value = rng.randint(low, high)
        elif kind == "bool":
            value = bool(rng.getrandbits(1))
        elif kind == "float":
            fraction = rng.random()
            value = (1 - fraction) * low + fraction * high
        else:
            value = rng.choice(args["choices"])
        state["random"] = {"counter": counter + 1}
        return result({"value": value}, modified=True)
    except (ValueError, KeyError, TypeError) as error:
        return failure(error)
