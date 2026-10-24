"""Get a file's immutable version reference without its content."""

from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "file_stat"
description = "Get a file's immutable version reference without its content."
schema = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    path = runtime.path(args["path"])
    if path not in runtime.files:
        raise ToolError(f"Virtual file not found: {path}")
    return {
        "path": path,
        "version": runtime.file_version(path),
        "bytes": len(runtime.files[path].encode()),
    }


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
