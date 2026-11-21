"""Compiler diagnostics that can be determined without tokenizing a source."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

import jsonschema
from pydantic import Field
from referencing import Registry
from referencing.exceptions import Unresolvable

from .compiler_schedule import EventSchedule, is_model_event
from .models import (
    DTO,
    ChatMessage,
    GraphSource,
    ProjectSettings,
    ToolCall,
    ToolResponse,
    WindowReference,
)


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class Diagnostic(DTO):
    severity: Literal["error", "warning"]
    code: str
    path: list[str | int]
    event_id: str | None = None
    message: str
    related_events: list[str] = Field(default_factory=list)


def dry_run(source: GraphSource, settings: ProjectSettings | None = None) -> list[Diagnostic]:
    """Check a source without assigning model ticks, token counts or physical slots."""
    return diagnose_schedule(EventSchedule(source), settings)


def diagnose_schedule(
    schedule: EventSchedule, settings: ProjectSettings | None
) -> list[Diagnostic]:
    diagnostics = []

    def emit(code, message, event=None, field=None, *, warning=False, related=(), path=None):
        diagnostics.append(
            Diagnostic(
                severity="warning" if warning else "error",
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
    for ident, dependencies in schedule.dependencies.items():
        event = events[ident]
        window = source.windows.get(event.window)
        if window is None:
            emit("missing_window", f"Unknown window: {event.window}", ident, "window")
        elif is_model_event(event) and not window.agent_writable:
            emit(
                "window_not_writable",
                "Agent output requires an agent-writable window",
                ident,
                "window",
            )
        elif isinstance(event, ChatMessage) and window.purpose != "chatroom":
            emit(
                "invalid_chat_window",
                'Chat messages need a window whose purpose is "chatroom"',
                ident,
                "window",
            )
        if len(event.after) != len(dependencies):
            emit(
                "duplicate_dependency",
                "Remove repeated dependency IDs",
                ident,
                "after",
                warning=True,
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
                if isinstance(piece, WindowReference) and piece.window not in source.windows:
                    emit(
                        "missing_window_reference",
                        f"Unknown referenced window: {piece.window}",
                        ident,
                        path=["events", ident, "content", index, "window"],
                    )
        if getattr(event, "replace_body", False) and not event.content:
            emit("empty_replacement", "Replacement must insert nonempty content", ident, "content")
        elif (
            getattr(event, "replace_body", False)
            and isinstance(event.content, list)
            and not any(isinstance(p, WindowReference) or p.text for p in event.content)
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
        window_ordered = check_window_order(ids)
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
        definition = source.windows.get(window)
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
                warning=True,
            )

    submitted = {}
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
                    warning=True,
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
                                    f"Submitted arguments could not be validated: {str(error)[:1000]}",
                                    ident,
                                    "content",
                                    warning=True,
                                )
                        else:
                            emit(
                                "tool_input_schema",
                                "Arguments contain logical references; schema check deferred",
                                ident,
                                "content",
                                warning=True,
                            )
            if event.close_window and not finished:
                cancelled = True
            if settings is not None and event.tool not in settings.tools:
                emit("unknown_tool", f"Unknown tool: {event.tool}", ident, "tool")
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
                    warning=True,
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
                if fragments[-1].tool != "chat":
                    continue
                try:
                    arguments = json.loads("".join(fragment.content for fragment in fragments))
                except (ValueError, TypeError):
                    continue  # Submission diagnostics handle malformed arguments.
                if isinstance(arguments, dict) and arguments.get("content") != event.content:
                    emit(
                        "chat_echo_mismatch",
                        "Chat echo differs from submitted content",
                        ident,
                        "content",
                    )
    return sorted(diagnostics, key=lambda d: (str(d.path), d.code))
