"""Diagnostic severities and the configurable warning catalogue."""

from .models import digest

ERRORS = frozenset(
    {
        "missing_window",
        "missing_dependency",
        "missing_window_reference",
        "dependency_cycle",
        "empty_replacement",
        "output_after_close",
        "unordered_window_writers",
        "unopened_window_reference",
        "read_only_window_write",
    }
)

WARNINGS = {
    "duplicate_dependency": (
        "Duplicate dependency",
        "An event lists the same prerequisite more than once.",
    ),
    "window_not_writable": (
        "Read-only window",
        "An agent event writes to a window marked read-only.",
    ),
    "invalid_chat_window": ("Chat window purpose", "A chat message is outside a chatroom."),
    "unknown_reasoning_level": (
        "Unknown reasoning level",
        "A window uses a reasoning level outside project settings.",
    ),
    "interleaved_framing": (
        "Interleaved framing",
        "Content crosses an unfinished tool call or response.",
    ),
    "multiple_tool_calls_in_window": (
        "Multiple tool calls in one window",
        "Distinct tool calls share one window. Give each call its own window.",
    ),
    "reasoning_in_tool_window": (
        "Reasoning in a tool window",
        "Agent reasoning shares a window with a tool call or result. Use a dedicated reasoning window.",
    ),
    "tool_result_unavailable": (
        "Tool result unavailable",
        "A direct tool response dependency has been closed or replaced before agent output.",
    ),
    "tools_window_has_dependencies": (
        "Tools window opens late",
        "The event that opens a tools window depends on other events.",
    ),
    "open_window": (
        "Open window",
        "A reasoning or tool window remains open without an acknowledgment.",
    ),
    "inconsistent_call": (
        "Inconsistent call",
        "Fragments of one call use different tools or windows.",
    ),
    "invalid_tool_begin": ("Call begin marker", "The call begin marker is missing or repeated."),
    "call_already_finished": (
        "Call already finished",
        "Another fragment follows submission or cancellation.",
    ),
    "unknown_tool": (
        "Unknown tool",
        "A call uses a tool outside the project catalogue or the visible tool listing.",
    ),
    "tool_input_schema": (
        "Tool arguments",
        "Submitted JSON fails the configured tool schema or cannot be checked.",
    ),
    "invalid_response_begin": (
        "Response begin marker",
        "The response begin marker is missing or repeated.",
    ),
    "response_already_finished": (
        "Response already finished",
        "Another fragment follows the response end marker.",
    ),
    "inconsistent_response": (
        "Inconsistent response",
        "Fragments of one response use different windows.",
    ),
    "unsubmitted_call": (
        "Unsubmitted call",
        "A response or echo lacks a causally prior submission.",
    ),
    "response_window": (
        "Response window",
        "A tool response uses a window outside its configured destination.",
    ),
    "chat_echo_disabled": (
        "Chat echo disabled",
        "A delivery is linked to a call with echo disabled.",
    ),
    "chat_echo_mismatch": (
        "Chat echo mismatch",
        "Delivered chat text differs from the submitted content.",
    ),
    "missing_tool_listing": (
        "Missing tool listing",
        "No tool schema listing is visible to a call.",
    ),
    "no_agent_activity": ("No agent activity", "The trajectory shows no meaningful agent output."),
    "missing_chat_echo": (
        "Missing chat echo",
        "A submitted chat call with echo enabled has no linked delivery.",
    ),
    "invalid_chat_echo_source": (
        "Invalid chat echo source",
        "A chat delivery links to a different synthetic tool.",
    ),
    "direct_chatroom_output": (
        "Direct chatroom output",
        "The agent writes text directly into a chatroom.",
    ),
}


def disabled(settings):
    return WARNINGS.keys() & set(settings.disabled_warnings) if settings is not None else set()


def choices():
    return [
        dict(code=code, name=name, description=description)
        for code, (name, description) in sorted(WARNINGS.items())
    ]


def identity(settings):
    """Inputs that can change a signoff's diagnostic findings."""
    return digest(
        {
            "errors": sorted(ERRORS),
            "warnings": sorted(WARNINGS),
            "disabled": sorted(disabled(settings)),
            "tools": {key: tool.model_dump(mode="json") for key, tool in settings.tools.items()},
            "reasoning_levels": sorted(set(settings.reasoning_levels)),
        }
    )
