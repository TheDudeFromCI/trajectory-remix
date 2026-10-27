"""Rank a synthetic corpus and return bounded source snippets."""

from ._web import COMMON, run_web, search

name = "web_search"
description = "Search synthetic pages by query. Returns ranked titles, URLs, stable source refs and verbatim snippets. Uses deterministic BM25 or authored exact-query rankings. max_results defaults to 5; offset/next_offset paginate. max_output_bytes defaults to 6000 (1024–16000), including JSON metadata. timeout_seconds defaults to 30 simulated seconds. Never accesses the internet."
schema = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 512},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        "offset": {"type": "integer", "minimum": 0, "maximum": 128},
        **COMMON,
    },
    "required": ["query"], "additionalProperties": False,
}


def run(args, state, windows):
    return run_web(name, args, state, search)
