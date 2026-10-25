"""Example custom tool that spells text as an uppercase JSON array."""

import json

name = "spell"
description = "Spell text as an array of uppercase characters."
schema = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "The text to spell out.",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}


def run(args: dict, state: dict, windows: list) -> tuple[str, bool, bool]:
    """Return the spelling without changing sandbox state or windows."""
    output = json.dumps(list(args["text"].upper()), ensure_ascii=False)
    return output, True, False
