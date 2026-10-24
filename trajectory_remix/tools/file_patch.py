"""Replace one unique text occurrence in a virtual file."""

from ._file_io import run_file_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "file_patch"
description = "Replace one unique text occurrence in a virtual file."
schema = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "old": {"type": "string"},
        "new": {"type": "string"},
        "expected_version": {"type": "string"},
    },
    "required": ["path", "old", "new"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    path = runtime.path(args["path"])
    if path not in runtime.files:
        raise ToolError(f"Virtual file not found: {path}")
    if not args["old"] or runtime.files[path].count(args["old"]) != 1:
        raise ToolError("Patch old text must match exactly once")
    return runtime.write_file(
        path,
        runtime.files[path].replace(args["old"], args["new"], 1),
        args.get("expected_version"),
    )


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_file_builtin(args, state, windows, _run, modifies=True)
