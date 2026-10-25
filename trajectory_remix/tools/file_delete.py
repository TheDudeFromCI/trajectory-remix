"""Delete a virtual file."""

from ._file_io import run_file_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "file_delete"
description = "Delete a virtual file."
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
    del runtime.files[path]
    return {"deleted": path}


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_file_builtin(args, state, windows, _run, modifies=True)
