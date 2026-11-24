"""Compiler diagnostics that can be determined without tokenizing a source."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

import jsonschema
from pydantic import Field
from referencing import Registry
from referencing.exceptions import Unresolvable

from .agent_share import low_agent_share_message
from .compiler_schedule import EventSchedule, is_model_event
from .diagnostic_policy import ERRORS, WARNINGS, disabled
from .models import (
    DTO,
    ChatMessage,
    GraphSource,
    ProjectSettings,
    ToolCall,
    ToolResponse,
    WindowReference,
)
from .tool_visibility import presentations, visible_at
from .window_evidence import evidence_blocker, invalidators


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class Diagnostic(DTO):
    severity: Literal["error", "warning"]
    code: str
    path: list[str | int]
    event_id: str | None = None
    message: str
    related_events: list[str] = Field(default_factory=list)


def has_content(content, *, whitespace=False):
    if isinstance(content, str):
        return bool(content if whitespace else content.strip())
    return any(
        isinstance(piece, WindowReference) or (piece.text if whitespace else piece.text.strip())
        for piece in content
    )


def content_text(content):
    if isinstance(content, str):
        return content
    if any(isinstance(piece, WindowReference) for piece in content):
        raise ValueError("Logical reference in text")
    return "".join(piece.text for piece in content)


def dry_run(source: GraphSource, settings: ProjectSettings | None = None) -> list[Diagnostic]:
    """Check a source without assigning model ticks, token counts or physical slots."""
    return diagnose_schedule(EventSchedule(source), settings)


def diagnose_schedule(
    schedule: EventSchedule, settings: ProjectSettings | None
) -> list[Diagnostic]:
    diagnostics = []
    suppressed = disabled(settings)

    def emit(code, message, event=None, field=None, *, related=(), path=None):
        if code in suppressed:
            return
        if code not in ERRORS and code not in WARNINGS:
            raise ValueError(f"Unregistered diagnostic: {code}")
        diagnostics.append(
            Diagnostic(
                severity="error" if code in ERRORS else "warning",
                code=code,
                event_id=event,
                message=message,
                related_events=list(related),
                path=path
                if path is not None
                else ["events", event, *([] if field is None else [field])],
            )
        )

    source = schedule.source
    events = source.events
    written = {event.window for event in events.values()}
    if not any(
        is_model_event(event)
        and (
            has_content(event.content)
            or (isinstance(event, ToolCall) and (event.insert_tool_begin or event.insert_tool_end))
        )
        for event in events.values()
    ):
        emit("no_agent_activity", "Add meaningful agent reasoning or a tool call", path=["events"])
    share_message = low_agent_share_message(source)
    if share_message:
        emit("low_agent_share", share_message, path=["events"])
    for ident, dependencies in schedule.dependencies.items():
        event = events[ident]
        window = source.windows.get(event.window)
        if window is None:
            emit("missing_window", f"Unknown window: {event.window}", ident, "window")
        elif isinstance(event, ChatMessage) and window.purpose != "chatroom":
            emit(
                "invalid_chat_window",
                'Chat messages need a window whose purpose is "chatroom"',
                ident,
                "window",
            )
        if event.kind == "agent_text" and window and window.purpose == "chatroom":
            emit(
                "direct_chatroom_output",
                "Agent text writes directly into the chatroom",
                ident,
                "window",
            )
        if len(event.after) != len(dependencies):
            emit(
                "duplicate_dependency",
                "Remove repeated dependency IDs",
                ident,
                "after",
            )
        for index, dep in enumerate(event.after):
            if dep not in events:
                emit(
                    "missing_dependency",
                    f"Unknown event: {dep}",
                    ident,
                    path=["events", ident, "after", index],
                )
        if isinstance(event.content, list):
            for index, piece in enumerate(event.content):
                if not isinstance(piece, WindowReference):
                    continue
                path = ["events", ident, "content", index, "window"]
                if piece.window not in source.windows:
                    emit(
                        "missing_window_reference",
                        f"Unknown referenced window: {piece.window}",
                        ident,
                        path=path,
                    )
                elif piece.window not in written:
                    emit(
                        "unopened_window_reference",
                        f"Referenced window never opens: {piece.window}",
                        ident,
                        path=path,
                    )
        if getattr(event, "replace_body", False) and not has_content(
            event.content, whitespace=True
        ):
            emit("empty_replacement", "Replacement must insert nonempty content", ident, "content")

    if settings is not None:
        for ident, window in sorted(source.windows.items()):
            if (
                window.reasoning_effort is not None
                and window.reasoning_effort not in settings.reasoning_levels
            ):
                emit(
                    "unknown_reasoning_level",
                    f"Unknown reasoning level: {window.reasoning_effort}",
                    path=["windows", ident, "reasoning_effort"],
                )

    ordered, ancestors, cyclic = schedule.causal_order()
    if cyclic:
        for ident in cyclic:
            emit("dependency_cycle", "Event belongs to a dependency cycle", ident, "after")
        # Lifecycle evaluation requires a DAG; broken cyclic drafts remain readable.
        return sorted(diagnostics, key=lambda d: (str(d.path), d.code))

    cleared = invalidators(source, ordered)
    for ident in ordered:
        event = events[ident]
        if not is_model_event(event) or (
            event.kind == "agent_text" and not has_content(event.content)
        ):
            continue
        for index, dependency in enumerate(event.after):
            result = events.get(dependency)
            if not isinstance(result, ToolResponse) or not has_content(result.content):
                continue
            blocker = evidence_blocker(source, ancestors, cleared, dependency, ident)
            if blocker:
                hidden_by, reason = blocker
                emit(
                    "tool_result_unavailable",
                    f"Tool response {dependency} is no longer visible here. Event {hidden_by} {reason}. "
                    "If this event uses the result, keep it visible or depend on a visible copy.",
                    ident,
                    path=["events", ident, "after", index],
                    related=list(dict.fromkeys([dependency, hidden_by])),
                )

    by_window, calls, responses = defaultdict(list), defaultdict(list), defaultdict(list)

    def check_window_order(ids):
        valid = True
        for index, left in enumerate(ids):
            for right in ids[index + 1 :]:
                if left not in ancestors[right] and right not in ancestors[left]:
                    emit(
                        "unordered_window_writers",
                        "Writers must be dependency ordered",
                        right,
                        "after",
                        related=[left],
                    )
                    valid = False
        return valid

    for ident in ordered:
        event = events[ident]
        by_window[event.window].append(ident)
        if isinstance(event, ToolCall):
            calls[event.call_id].append(ident)
        elif isinstance(event, ToolResponse):
            responses[event.call_id].append(ident)
    for window, ids in sorted(by_window.items()):
        definition = source.windows.get(window)
        first_call_events = {}
        has_result = False
        for ident in ids:
            event = events[ident]
            if isinstance(event, ToolCall):
                first_call_events.setdefault(event.call_id, ident)
            elif isinstance(event, ToolResponse):
                has_result = True
        if len(first_call_events) > 1:
            emit(
                "multiple_tool_calls_in_window",
                f"Window {window} contains {len(first_call_events)} distinct tool calls. Give each call its own window.",
                path=["windows", window],
                related=first_call_events.values(),
            )
        if first_call_events or has_result or (definition and definition.purpose == "tool"):
            for ident in ids:
                event = events[ident]
                if event.kind == "agent_text" and has_content(event.content):
                    emit(
                        "reasoning_in_tool_window",
                        "Move reasoning to a dedicated window and preserve its causal dependencies.",
                        ident,
                        "window",
                    )
        window_ordered = check_window_order(ids)
        if definition and definition.purpose == "tools" and window_ordered and events[ids[0]].after:
            emit(
                "tools_window_has_dependencies",
                "Open the tools window at the start by giving its first event an empty after list.",
                ids[0],
                "after",
                related=events[ids[0]].after,
            )
        if definition and not definition.agent_writable and window_ordered:
            opened_read_only = not is_model_event(events[ids[0]])
            for ident in ids:
                event = events[ident]
                if not is_model_event(event):
                    if event.close_window:
                        break
                    continue
                emits_rows = (
                    event.close_window
                    or has_content(event.content, whitespace=True)
                    or (
                        isinstance(event, ToolCall)
                        and (event.insert_tool_begin or event.insert_tool_end)
                    )
                )
                if opened_read_only and emits_rows:
                    emit(
                        "read_only_window_write",
                        "Agent output cannot be compiled after external input opens a read-only window",
                        ident,
                        "window",
                        related=[ids[0]],
                    )
                else:
                    emit(
                        "window_not_writable",
                        "Agent event uses a window marked read-only",
                        ident,
                        "window",
                    )
                if event.close_window:
                    break
        open_frame = None
        if window_ordered:
            for ident in ids:
                event = events[ident]
                if isinstance(event, (ToolCall, ToolResponse)):
                    frame = (event.kind, event.call_id)
                    begin = (
                        event.insert_tool_begin
                        if isinstance(event, ToolCall)
                        else event.insert_response_begin
                    )
                    end = (
                        event.insert_tool_end
                        if isinstance(event, ToolCall)
                        else event.insert_response_end
                    )
                    if open_frame is not None and open_frame != frame:
                        emit(
                            "interleaved_framing",
                            "Another request/response frame is still open in this window",
                            ident,
                            "call_id",
                        )
                    if begin:
                        open_frame = frame
                    if end:
                        open_frame = None
                elif open_frame is not None and event.content:
                    emit(
                        "interleaved_framing",
                        "Content inside an open frame must be an explicit fragment",
                        ident,
                        "content",
                    )
                if event.close_window:
                    open_frame = None
        for index, ident in enumerate(ids):
            if events[ident].close_window:
                for later in ids[index + 1 :]:
                    if ident in ancestors[later]:
                        emit(
                            "output_after_close",
                            "Logical windows cannot reopen after closure",
                            later,
                            "window",
                            related=[ident],
                        )
        if (
            definition
            and definition.purpose in ("reasoning", "tool")
            and not definition.intentionally_left_open
            and not any(events[i].close_window for i in ids)
        ):
            emit(
                "open_window",
                "Reasoning/tool window remains open",
                ids[-1],
                "close_window",
            )

    submitted = {}
    listings = presentations(source, ordered)
    for call_id, ids in sorted(calls.items()):
        ordered_call = all(left in ancestors[right] for left, right in zip(ids, ids[1:]))
        first = events[ids[0]]
        finished, cancelled = False, False
        for index, ident in enumerate(ids):
            event = events[ident]
            if event.tool != first.tool or event.window != first.window:
                emit(
                    "inconsistent_call",
                    "Fragments must share tool and request window",
                    ident,
                    "call_id",
                )
            if event.insert_tool_begin != (index == 0):
                emit(
                    "invalid_tool_begin",
                    "Only the first fragment must insert tool begin",
                    ident,
                    "insert_tool_begin",
                )
            if finished or cancelled:
                emit(
                    "call_already_finished",
                    "Cannot append to a submitted or cancelled request",
                    ident,
                    "call_id",
                )
            if event.insert_tool_end and not finished and not cancelled:
                finished = True
                if ordered_call:
                    submitted[call_id] = ident
                if settings is not None and ordered_call:
                    definition = settings.tools.get(event.tool)
                    if definition is not None:
                        fragments = [events[i].content for i in ids[: index + 1]]
                        if all(
                            isinstance(f, str) or all(p.kind == "text" for p in f)
                            for f in fragments
                        ):
                            text = "".join(
                                f if isinstance(f, str) else "".join(p.text for p in f)
                                for f in fragments
                            )
                            try:
                                args = json.loads(text, parse_constant=reject_constant)
                                # Local references work, but authored schemas
                                # cannot trigger network I/O while reading a graph.
                                jsonschema.Draft202012Validator(
                                    definition.input_schema, registry=Registry()
                                ).validate(args)
                            except (
                                ValueError,
                                jsonschema.ValidationError,
                                Unresolvable,
                                RecursionError,
                            ) as error:
                                emit(
                                    "tool_input_schema",
                                    f"Submitted arguments do not match the configured JSON schema: {getattr(error, 'message', str(error))[:1000]}",
                                    ident,
                                    "content",
                                )
                        else:
                            emit(
                                "tool_input_schema",
                                "Arguments contain logical references; schema check deferred",
                                ident,
                                "content",
                            )
            if event.close_window and not finished:
                cancelled = True
            names, visible = visible_at(listings, ancestors[ident])
            if not visible:
                emit(
                    "missing_tool_listing",
                    "Show a tool listing before this call and add it as a dependency",
                    ident,
                    "after",
                )
            if settings is not None and event.tool not in settings.tools:
                emit(
                    "unknown_tool",
                    f"Tool is absent from Project Settings: {event.tool}",
                    ident,
                    "tool",
                )
            elif visible and event.tool not in names:
                emit(
                    "unknown_tool",
                    f"Tool is absent from the visible listing: {event.tool}",
                    ident,
                    "tool",
                    related=visible,
                )
        # Intentionally abandoned prefixes (including invalid JSON) are legal.

    for call_id, ids in sorted(responses.items()):
        first = events[ids[0]]
        ended = False
        for index, ident in enumerate(ids):
            event = events[ident]
            if event.insert_response_begin != (index == 0):
                emit(
                    "invalid_response_begin",
                    "Only the first response fragment must insert response begin",
                    ident,
                    "insert_response_begin",
                )
            if ended:
                emit(
                    "response_already_finished",
                    "Cannot append after response end",
                    ident,
                    "call_id",
                )
            ended |= event.insert_response_end
            if event.window != first.window:
                emit(
                    "inconsistent_response",
                    "Response fragments must share a window",
                    ident,
                    "window",
                )
            submission = submitted.get(call_id)
            if submission is None or submission not in ancestors[ident]:
                emit(
                    "unsubmitted_call",
                    "Response requires an ancestor submitted request",
                    ident,
                    "call_id",
                )
            if settings is not None and call_id in calls:
                definition = settings.tools.get(events[calls[call_id][0]].tool)
                if (
                    definition
                    and definition.response_window
                    and event.window != definition.response_window
                ):
                    emit(
                        "response_window",
                        "Response does not target the configured window",
                        ident,
                        "window",
                    )

    delivered = set()
    for ident in ordered:
        event = events[ident]
        if isinstance(event, ChatMessage) and event.call_id is not None:
            submission = submitted.get(event.call_id)
            if submission is None or submission not in ancestors[ident]:
                emit(
                    "unsubmitted_call",
                    "Chat echo requires an ancestor submitted request",
                    ident,
                    "call_id",
                )
            elif events[submission].tool != "chat":
                emit(
                    "invalid_chat_echo_source",
                    "Chat echo links to a tool other than chat",
                    ident,
                    "call_id",
                    related=[submission],
                )
            elif not all(events[i].echo_chat for i in calls[event.call_id]):
                emit(
                    "chat_echo_disabled",
                    "Linked chat echo is disabled for this call",
                    ident,
                    "call_id",
                )
            else:
                # Repairs must preserve exact linked automatic deliveries.
                fragment_ids = sorted(calls[event.call_id], key=lambda key: len(ancestors[key]))
                fragments = [events[key] for key in fragment_ids]
                delivered.add(event.call_id)
                try:
                    arguments = json.loads(
                        "".join(content_text(fragment.content) for fragment in fragments)
                    )
                except (ValueError, TypeError):
                    continue  # Submission diagnostics handle malformed arguments.
                if isinstance(arguments, dict) and arguments.get("content") != event.content:
                    emit(
                        "chat_echo_mismatch",
                        "Chat echo differs from submitted content",
                        ident,
                        "content",
                    )
    for call_id, ident in submitted.items():
        if (
            events[ident].tool == "chat"
            and all(events[i].echo_chat for i in calls[call_id])
            and call_id not in delivered
        ):
            emit(
                "missing_chat_echo",
                "Add a linked chat delivery after submission or disable echo",
                ident,
                "echo_chat",
            )
    return sorted(diagnostics, key=lambda d: (str(d.path), d.code))
