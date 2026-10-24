"""Author-only simulation of permission grants and user rejections."""

import copy
from ._access import MATCH_PROPERTIES
from ._simulation import result

name = "tool_permissions_set"
invisible = True
description = "Replace simulated tool permissions. Default allow. First matching rule wins; tool/path are case-sensitive globs, remaining optionally limits dispatches. Denials return Permission denied without executing. Invisible author calls bypass permission checks."
schema = {
    "type": "object",
    "properties": {
        "default": {"type": "string", "enum": ["allow", "deny"]},
        "rules": {
            "type": "array",
            "maxItems": 256,
            "items": {
                "type": "object",
                "properties": {
                    **MATCH_PROPERTIES,
                    "decision": {"type": "string", "enum": ["allow", "deny"]},
                },
                "required": ["decision"],
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def run(args, state, windows):
    config = {
        "default": args.get("default", "allow"),
        "rules": copy.deepcopy(args.get("rules", [])),
    }
    state["tool_permissions"] = config
    return result(config, modified=True)
