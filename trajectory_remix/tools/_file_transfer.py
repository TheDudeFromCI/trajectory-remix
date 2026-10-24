"""Atomic single-file copy/move operations shared by the three public tools."""

from ._protocol import ToolError

SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "destination": {"type": "string"},
        "overwrite": {"type": "boolean"},
        "expected_version": {"type": "string"},
    },
    "required": ["source", "destination"],
    "additionalProperties": False,
}


def transfer(runtime, args, *, move):
    source, destination = (
        runtime.path(args["source"]),
        runtime.path(args["destination"]),
    )
    if source == destination:
        raise ToolError("Source and destination must differ")
    if source not in runtime.files:
        raise ToolError(f"Virtual file not found: {source}")
    if destination in runtime.files and not args.get("overwrite", False):
        raise ToolError(f"Destination already exists: {destination}")
    if (
        "expected_version" in args
        and runtime.file_version(source) != args["expected_version"]
    ):
        raise ToolError("File version conflict")
    original = runtime.files
    content = original[source]
    if move:
        runtime.files = {
            path: text for path, text in original.items() if path != source
        }
    try:
        saved = runtime.write_file(destination, content)
    except Exception:
        runtime.files = original
        raise
    return {"source": source, "destination": destination, "version": saved["version"]}
