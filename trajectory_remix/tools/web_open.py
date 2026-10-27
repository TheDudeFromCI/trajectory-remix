"""Read a bounded excerpt from an immutable synthetic page snapshot."""

from ._web import COMMON, SOURCE, SOURCE_CHOICE, open_page, run_web

name = "web_open"
description = "Read a synthetic page by exactly one of url or ref. Returns readable source text, status, stable ref, line positions and links. start_line is 1-based; start_column is 0-based for continuing a long line. max_lines defaults to 40. Continue with next_line/next_column; paginate links with link_offset/next_link_offset (max_links defaults to 5, 0 omits links). max_output_bytes defaults to 6000 including JSON metadata; timeout_seconds defaults to 30 simulated seconds. No real network access."
schema = {
    "type": "object",
    "properties": {
        **SOURCE,
        "start_line": {"type": "integer", "minimum": 1},
        "start_column": {"type": "integer", "minimum": 0},
        "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
        "link_offset": {"type": "integer", "minimum": 0, "maximum": 128},
        "max_links": {"type": "integer", "minimum": 0, "maximum": 20},
        **COMMON,
    },
    "oneOf": SOURCE_CHOICE, "additionalProperties": False,
}


def run(args, state, windows):
    return run_web(name, args, state, open_page)
