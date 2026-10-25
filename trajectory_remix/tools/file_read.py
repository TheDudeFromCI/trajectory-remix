"""Read a virtual text file."""

from ._file_io import run_file_builtin
from ._protocol import ToolError
from .runtime import WindowProperties

name = "file_read"
description = "Read a virtual text file or historical version. Optional start_line/end_line are inclusive, 1-based. line_numbers adds line prefixes."
schema = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "version": {"type": "string"},
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "line_numbers": {"type": "boolean"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    path = runtime.path(args["path"])
    if "version" in args:
        key = runtime.artifact_key(path, args["version"])
        if key not in runtime.artifacts:
            raise ToolError("Artifact version not found")
        value = {
            "path": path,
            "version": args["version"],
            "content": runtime.artifacts[key],
        }
    else:
        if path not in runtime.files:
            raise ToolError(f"Virtual file not found: {path}")
        value = {
            "path": path,
            "version": runtime.file_version(path),
            "content": runtime.files[path],
        }
    if any(key in args for key in ("start_line", "end_line", "line_numbers")):
        lines = value["content"].splitlines(keepends=True)
        start, end = args.get("start_line", 1), args.get("end_line", len(lines))
        if "end_line" in args and end < start:
            raise ToolError("end_line must be at least start_line")
        selected = lines[start - 1 : end]
        value.update(
            start_line=start, end_line=min(end, len(lines)), total_lines=len(lines)
        )
        value["content"] = "".join(
            f"{index}: {line}" if args.get("line_numbers") else line
            for index, line in enumerate(selected, start)
        )
    return value


def run(
    args: dict, state: dict, windows: list[WindowProperties]
) -> tuple[str, bool, bool]:
    return run_file_builtin(args, state, windows, _run, modifies=False)
