"""Read a virtual text file."""

from ._support import run_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "file_read"
description = "Read a virtual text file."
schema = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "version": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    path = runtime.path(args["path"])
    if "version" in args:
        key = runtime.artifact_key(path, args["version"])
        if key not in runtime.artifacts:
            raise ToolError("Artifact version not found")
        return {
            "path": path,
            "version": args["version"],
            "content": runtime.artifacts[key],
        }
    if path not in runtime.files:
        raise ToolError(f"Virtual file not found: {path}")
    return {
        "path": path,
        "version": runtime.file_version(path),
        "content": runtime.files[path],
    }


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=False)
