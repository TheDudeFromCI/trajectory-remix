"""List all virtual files."""

from ._file_io import run_file_builtin
from .runtime import WindowProperties
from fnmatch import fnmatchcase
from ._access import readable_path
from .runtime import execution_context

name = "file_list"
description = "List virtual file paths in sorted order. Optional glob filters paths (for example /src/*.py)."
schema = {
    "type": "object",
    "properties": {"glob": {"type": "string", "maxLength": 1024}},
    "required": [],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    pattern = "/" + args.get("glob", "*").lstrip("/")
    state = {"tool_permissions": runtime.tool_permissions}
    return [
        path
        for path in sorted(runtime.files)
        if fnmatchcase(path, pattern)
        and (execution_context.get().invisible or readable_path(state, name, path))
    ]


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_file_builtin(args, state, windows, _run, modifies=False)
