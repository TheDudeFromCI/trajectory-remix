"""Retrieve memories ranked by lexical overlap (local RAG)."""

import re
from ._support import run_builtin
from .runtime import WindowProperties

name = "memory_search"
description = "Retrieve memories ranked by lexical overlap (local RAG)."
schema = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    terms = set(re.findall("\\w+", args["query"].lower()))
    ranked = []
    for key, text in runtime.memory.items():
        words = set(re.findall("\\w+", text.lower()))
        score = len(terms & words) / max(1, len(terms | words))
        if score:
            ranked.append({"key": key, "text": text, "score": score})
    return sorted(ranked, key=lambda r: (-r["score"], r["key"]))[:8]


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
