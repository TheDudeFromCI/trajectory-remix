"""Write a UTF-8 text file inside the virtual RAM filesystem."""

from ._support import run_builtin
from .runtime import WindowProperties

name = "file_write"
description = "Write a UTF-8 text file inside the virtual RAM filesystem."
schema = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "expected_version": {"type": "string"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    path = runtime.path(args["path"])
    return runtime.write_file(path, args["content"], args.get("expected_version"))


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_builtin(args, state, windows, _run, modifies=True)
