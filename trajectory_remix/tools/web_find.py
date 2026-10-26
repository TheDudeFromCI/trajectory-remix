"""Locate literal text in a synthetic page without returning the whole page."""

from ._web import COMMON, SOURCE, SOURCE_CHOICE, find, run_web

name = "web_find"
description = "Find literal text within a synthetic page by exactly one of url or ref. Case-insensitive by default. Returns matching lines, 0-based columns and short source excerpts; at most one hit per line. start_line/next_line paginate. max_results defaults to 10; context_lines to 1. Excerpt lines are shortened to 240–360 characters; use web_open for full passages. max_output_bytes defaults to 6000 including JSON metadata; timeout_seconds defaults to 30 simulated seconds. No real network access."
schema = {
    "type": "object",
    "properties": {
        **SOURCE,
        "text": {"type": "string", "minLength": 1, "maxLength": 512},
        "case_sensitive": {"type": "boolean"},
        "start_line": {"type": "integer", "minimum": 1},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        "context_lines": {"type": "integer", "minimum": 0, "maximum": 3},
        **COMMON,
    },
    "required": ["text"], "oneOf": SOURCE_CHOICE, "additionalProperties": False,
}


def run(args, state, windows):
    return run_web(name, args, state, find)
