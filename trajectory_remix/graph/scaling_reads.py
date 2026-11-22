"""Paged reads and section boundaries for the scaling experiment."""

from pydantic import Field

from . import authoring, scaling
from .authoring_tools import CausalClaim, EventID, WindowID
from .models import DTO, Identifier, canonical

PAGE = 16
TEXT = 4800


class Offset(DTO):
    offset: int = Field(default=0, ge=0)


class ReadEvent(EventID):
    content_offset: int = Field(default=0, ge=0)
    visible_offset: int = Field(default=0, ge=0)
    dependency_offset: int = Field(default=0, ge=0)


class ReadWindow(WindowID, Offset):
    pass


class ReadSection(Offset):
    section: int = Field(ge=1)
    event_offset: int = Field(default=0, ge=0)


class ReadTool(Offset):
    tool_id: Identifier


class Checkpoint(DTO):
    summary: str = Field(min_length=1, max_length=1200)
    causal_claims: list[CausalClaim] = Field(default_factory=list)
    complete: bool = True


READS = {
    "view_graph": (Offset, "List a page of event IDs, windows and prerequisites."),
    "view_window": (ReadWindow, "List a page of events in a window. Read bodies with view_event."),
    "view_event": (ReadEvent, "Read one page of an event body and its causal visibility."),
    "view_diagnostics": (Offset, "Read one page of errors and enabled warnings."),
    "view_section": (
        ReadSection,
        "Read a previous section's milestone, event IDs and Draft when available. Section is 1-based.",
    ),
    "view_project_tool": (ReadTool, "Read one project tool definition by its exact ID."),
}


def tool(name, dto, description):
    return dict(
        type="function",
        function=dict(name=name, description=description, parameters=dto.model_json_schema()),
    )


def tools(base, *, checkpoint=False, sections=True):
    result = []
    for entry in base:
        name = entry["function"]["name"]
        result.append(tool(name, *READS[name]) if name in READS else entry)
    if sections:
        result.append(tool("view_section", *READS["view_section"]))
    result.append(tool("view_project_tool", *READS["view_project_tool"]))
    if checkpoint:
        result = [entry for entry in result if entry["function"]["name"] != "signoff"]
        result.append(
            tool(
                "checkpoint_section",
                Checkpoint,
                "Commit the current section and its handoff before a fresh conversation. Set complete to false for a partial checkpoint. Call last.",
            )
        )
    return result


def page(items, offset, limit=PAGE):
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


def read(source, settings, session, name, args):
    payload = READS[name][0].model_validate(args)
    if name == "view_project_tool":
        definition = settings.tools[payload.tool_id].model_dump(mode="json")
        return dict(definition=page(canonical(definition), payload.offset, TEXT))
    if name == "view_graph":
        rows = page(sorted(source.events), payload.offset)
        entries = []
        for key in rows["items"]:
            event = source.events[key]
            entries.append(
                dict(
                    id=key,
                    window=event.window,
                    kind=event.kind,
                    after=event.after[:PAGE],
                    dependency_count=len(event.after),
                    close_window=event.close_window,
                )
            )
        rows["items"] = entries
        return dict(events=rows, window_count=len(source.windows))
    if name == "view_window":
        window = source.windows[payload.window_id]
        events = sorted(key for key, e in source.events.items() if e.window == payload.window_id)
        return dict(window=window.model_dump(mode="json"), events=page(events, payload.offset))
    if name == "view_diagnostics":
        return dict(diagnostics=page(authoring.findings(source, settings), payload.offset))
    if name == "view_section":
        if not session.get("scaling") or not scaling.is_scaled(source):
            raise ValueError("Sections are only available on experimental trajectories")
        if payload.section > len(source.metadata["scaling"]["outline"]):
            raise ValueError("Unknown section")
        entry = scaling.section(
            source, payload.section - 1, with_draft=session.get("draft_visible", False)
        )
        entry["events"] = page(entry["events"], payload.event_offset)
        if "draft" in entry:
            entry["draft"] = page(entry["draft"], payload.offset, TEXT)
        context = session["scaling"]
        if isinstance(context, dict):
            entry["handoffs"] = context["notes"].get(str(payload.section), [])
        return entry
    _, reply = authoring.apply(source, settings, "view_event", {"event_id": payload.event_id})
    event, visible = reply["event"], reply["visible"]
    content = event.pop("content")
    if not isinstance(content, str):
        content = canonical(content)
        event["content_encoding"] = "json"
    event["content"] = page(content, payload.content_offset, TEXT)
    event["after"] = page(event["after"], payload.dependency_offset)
    if visible is not None:
        visible = page(
            [(window, key) for window, ids in visible.items() for key in ids],
            payload.visible_offset,
        )
    return dict(event=event, visible=visible)
