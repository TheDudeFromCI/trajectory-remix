"""Author-only deterministic I/O fault injection."""

import copy
from ._access import MATCH_PROPERTIES
from ._simulation import result

name = "io_failure_set"
invisible = True
description = "Replace simulated I/O failure rules. First matching rule wins; tool/path are globs. remaining makes a transient failure (otherwise repeats). code may be EIO, ENOSPC, EACCES, ETIMEDOUT, etc. Empty rules clears failures. Applied once at dispatch, before side effects, to file operations, watchers and network_request."
schema = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "maxItems": 256,
            "items": {
                "type": "object",
                "properties": {
                    **MATCH_PROPERTIES,
                    "code": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                "additionalProperties": False,
            },
        },
    },
    "required": ["rules"],
    "additionalProperties": False,
}


def run(args, state, windows):
    state["io_failures"] = {"rules": copy.deepcopy(args["rules"])}
    return result(state["io_failures"], modified=True)
