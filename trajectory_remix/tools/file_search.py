"""Search virtual file content with bounded, line-numbered results."""

import re
from fnmatch import fnmatchcase
from ._file_io import run_file_builtin
from ._protocol import ToolError
from ._access import readable_path
from .runtime import execution_context

name = "file_search"
description = "Search virtual files by literal text (default) or regex. Optional path glob, case_sensitive, context_lines and max_results. Returns line numbers, context and a truncated indicator in stable path/line order."
schema = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 4096},
        "glob": {"type": "string", "maxLength": 1024},
        "regex": {"type": "boolean"},
        "case_sensitive": {"type": "boolean"},
        "context_lines": {"type": "integer", "minimum": 0, "maximum": 20},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _run(runtime, args, windows):
    try:
        pattern = re.compile(
            args["query"] if args.get("regex") else re.escape(args["query"]),
            0 if args.get("case_sensitive", True) else re.IGNORECASE,
        )
    except re.error as error:
        raise ToolError(f"Invalid search regex: {error}") from error
    matches, limit, context = (
        [],
        args.get("max_results", 100),
        args.get("context_lines", 0),
    )
    for path, content in sorted(runtime.files.items()):
        if not fnmatchcase(path, "/" + args.get("glob", "*").lstrip("/")):
            continue
        if not execution_context.get().invisible and not readable_path(
            {"tool_permissions": runtime.tool_permissions}, name, path
        ):
            continue
        lines = content.splitlines()
        for index, line in enumerate(lines):
            if pattern.search(line):
                if len(matches) == limit:
                    return {"matches": matches, "truncated": True}
                matches.append(
                    {
                        "path": path,
                        "line": index + 1,
                        "text": line,
                        "before": lines[max(0, index - context) : index],
                        "after": lines[index + 1 : index + 1 + context],
                    }
                )
    return {"matches": matches, "truncated": False}


def run(args, state, windows):
    return run_file_builtin(args, state, windows, _run)
