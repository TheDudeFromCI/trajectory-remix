"""Replace one unique text occurrence in a virtual file."""

from ._file_io import run_file_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "file_patch"
description = "Apply exact text edits atomically. Supply old/new or an edits array of old/new pairs, applied in order. Each old must match exactly once. Optional expected_version detects stale edits."
schema = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "old": {"type": "string"},
        "new": {"type": "string"},
        "expected_version": {"type": "string"},
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": {
                "type": "object",
                "properties": {
                    "old": {"type": "string", "minLength": 1},
                    "new": {"type": "string"},
                },
                "required": ["old", "new"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["path"],
    "oneOf": [
        {"required": ["old", "new"], "not": {"required": ["edits"]}},
        {
            "required": ["edits"],
            "not": {"anyOf": [{"required": ["old"]}, {"required": ["new"]}]},
        },
    ],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    path = runtime.path(args["path"])
    if path not in runtime.files:
        raise ToolError(f"Virtual file not found: {path}")
    content = runtime.files[path]
    for edit in args.get("edits", [args]):
        if not edit["old"] or content.count(edit["old"]) != 1:
            raise ToolError("Patch old text must match exactly once")
        content = content.replace(edit["old"], edit["new"], 1)
    return runtime.write_file(
        path,
        content,
        args.get("expected_version"),
    )


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_file_builtin(args, state, windows, _run, modifies=True)
