"""Author-only configuration of simulated file-I/O latency."""

from ._simulation import result

name = "hardware_load_set"
invisible = True
description = "Configure extra execution ticks for file operations. file_io_ticks is the default; operation_ticks overrides individual operations. Applies to newly started calls; legacy duration-ticks is additive. Omitted settings are retained."
FILE_OPERATIONS = (
    "file_read",
    "file_write",
    "file_patch",
    "file_delete",
    "file_stat",
    "file_list",
)
schema = {
    "type": "object",
    "properties": {
        "file_io_ticks": {"type": "integer", "minimum": 0, "maximum": 99999},
        "operation_ticks": {
            "type": "object",
            "properties": {
                key: {"type": "integer", "minimum": 0, "maximum": 99999}
                for key in FILE_OPERATIONS
            },
            "additionalProperties": False,
        },
    },
    "minProperties": 1,
    "additionalProperties": False,
}


def run(args, state, windows):
    previous = state.get("hardware_load", {})
    updated = {**previous, **args}
    if "operation_ticks" in args:
        updated["operation_ticks"] = {
            **previous.get("operation_ticks", {}),
            **args["operation_ticks"],
        }
    state["hardware_load"] = updated
    return result(updated, modified=True)
