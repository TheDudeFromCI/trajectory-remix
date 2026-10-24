"""Tool modules and the public run(args, state, windows) tick protocol."""

from ._protocol import (
    ToolError,
    tool_failure,
    validate_subagent_outcome,
    tool_text,
    tool_json,
    tool_message,
    tool_request,
    chat_context,
)
from .runtime import (
    Sandbox,
    Tool,
    ToolRegistry,
    WindowProperties,
    ExecutionContext,
    default_registry,
    execution_context,
    public_schema,
)

# Compatibility exports for clients that used the built-in simulation helpers.
STORES = ("files", "memory", "notes", "skills", "artifacts", "conversations")
SCHEMAS = default_registry().schemas()
DEFINITIONS = [
    (
        s["function"]["name"],
        s["function"]["description"],
        {
            k: v
            for k, v in s["function"]["parameters"]["properties"].items()
            if k in s["function"]["parameters"].get("required", [])
        },
    )
    for s in SCHEMAS
]
