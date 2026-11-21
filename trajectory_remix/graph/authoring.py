"""Source-only edits and reads shared by providers and MCP."""

from .authoring_tools import READ_ONLY, TOOLS
from .compiler_schedule import EventSchedule
from .flow import causal_view
from .models import GraphSource, canonical
from .tool_listing import listing, presented_ids
from .validation import diagnose

SCHEMA_ID = "tools"
EVENT_FIELDS = {
    "agent_text": set(),
    "external_text": {"delivery", "replace_body"},
    "tool_schema": {"delivery", "replace_body", "extra_tools"},
    "tool_call": {"call_id", "tool", "insert_tool_begin", "insert_tool_end", "echo_chat"},
    "tool_response": {
        "delivery",
        "call_id",
        "insert_response_begin",
        "insert_response_end",
        "replace_body",
    },
    "chat_message": {"delivery", "chat", "call_id"},
}
COMMON_FIELDS = {"kind", "window", "after", "content", "close_window", "supervision"}


def findings(source, settings):
    return [diagnostic.model_dump(mode="json") for diagnostic in diagnose(source, settings)]


def references(events):
    windows = {event["window"] for event in events.values()}
    for event in events.values():
        if isinstance(event["content"], list):
            windows.update(p["window"] for p in event["content"] if p["kind"] == "window_ref")
    return windows


def _default_window(kind):
    purpose, writable = {
        "agent_text": ("reasoning", True),
        "tool_call": ("tool", True),
        "tool_response": ("tool", False),
        "chat_message": ("chatroom", False),
        "tool_schema": ("tools", False),
    }.get(kind, ("document", False))
    return dict(purpose=purpose, agent_writable=writable)


def _merge_event(existing, changes):
    kind = changes.get("kind", existing["kind"])
    if kind not in EVENT_FIELDS:
        raise ValueError("Use a supported event kind")
    supported = COMMON_FIELDS | EVENT_FIELDS[kind]
    if changes.keys() - supported:
        raise ValueError(
            "Fields do not apply to this event kind: "
            + ", ".join(sorted(changes.keys() - supported))
        )
    edited = {key: value for key, value in existing.items() if key in supported}
    for key, value in changes.items():
        if key in ("supervision", "delivery", "chat") and isinstance(value, dict):
            edited[key] = edited.get(key, {}) | value
        else:
            edited[key] = value
    return edited


def _ensure_windows(data, event, new_properties=None):
    windows = data["windows"]
    window_id = event["window"]
    if new_properties is not None and window_id in windows:
        raise ValueError("Use configure_window to change an existing window")
    windows.setdefault(window_id, _default_window(event["kind"]) | (new_properties or {}))
    for ident in references({"event": event}) - {window_id}:
        windows.setdefault(ident, _default_window("external_text"))


def _schema_event(data):
    existing = data["events"].get(SCHEMA_ID)
    if existing is not None and (
        existing["window"] != SCHEMA_ID or existing["kind"] not in {"tool_schema", "external_text"}
    ):
        raise ValueError("The reserved tools event ID is occupied")
    return existing


def _link_listing(data, event_ids):
    # The reserved listing is a root. Adding it to a call's predecessors
    # preserves all existing ancestry and cannot create a cycle.
    events = data["events"]
    if len(event_ids) > 1:
        ordered, _, _ = EventSchedule(GraphSource.model_validate(data)).causal_order()
        selected = set(event_ids)
        event_ids = [ident for ident in ordered if ident in selected] + sorted(
            selected - set(ordered)
        )
    for ident in event_ids:
        after = events[ident].setdefault("after", [])
        todo, visited = list(after), set()
        while todo:
            dep = todo.pop()
            if dep == SCHEMA_ID:
                break
            if dep not in visited:
                visited.add(dep)
                todo.extend(events.get(dep, {}).get("after", []))
        else:
            after.append(SCHEMA_ID)


def _post_schema(data, settings, tool_ids):
    _schema_event(data)
    window = data["windows"].get(SCHEMA_ID)
    if window and (window["purpose"] != "tools" or window["agent_writable"]):
        raise ValueError("Configure the reserved tools window as read-only with purpose tools")
    data["windows"].setdefault(SCHEMA_ID, _default_window("tool_schema"))
    catalog = {key: tool.model_dump(mode="json") for key, tool in settings.tools.items()}
    blob = listing(catalog, data["id"], SCHEMA_ID, tool_ids, 0)
    data["events"][SCHEMA_ID] = dict(
        kind="tool_schema",
        window=SCHEMA_ID,
        after=[],
        content=blob["content"],
        supervision=dict(masked=True),
    )
    if "shown_tools" in data.get("metadata", {}):
        data["metadata"]["shown_tools"] = blob["shown"]
    _link_listing(
        data, [ident for ident, event in data["events"].items() if event["kind"] == "tool_call"]
    )


def _sync_call(data, settings, event_id):
    event = data["events"][event_id]
    if event["kind"] != "tool_call":
        return
    existing = _schema_event(data)
    if event["tool"] in settings.tools:
        # Read actual source declarations. Imports may contain unknown or malformed entries.
        old = GraphSource.model_validate(data).events.get(SCHEMA_ID)
        shown = presented_ids(old.content) if old else set()
        if event["tool"] not in shown or old.after or old.close_window:
            _post_schema(data, settings, sorted((shown & settings.tools.keys()) | {event["tool"]}))
            return
    if existing and not existing.get("after") and not existing.get("close_window"):
        _link_listing(data, [event_id])


def _post_chat(data, settings, args):
    ident = args.event_id
    if args.window not in data["windows"]:
        data["windows"][args.window] = _default_window("chat_message")
    room = data["windows"][args.window]
    if room["purpose"] != "chatroom" or room["agent_writable"]:
        raise ValueError("Choose a read-only chatroom window")
    events = data["events"]
    after = list(
        dict.fromkeys(
            [*args.after, *(key for key, event in events.items() if event["window"] == args.window)]
        )
    )
    call_id = None
    if args.origin == "agent":
        if "chat" not in settings.tools:
            raise ValueError("The project needs the synthetic chat tool for agent chat")
        call_id = ident + ":call"
        request_window = ident + ":chat"
        if call_id in events or request_window in data["windows"] or request_window == args.window:
            raise ValueError("Choose an unused chat event ID")
        events[call_id] = dict(
            kind="tool_call",
            tool="chat",
            call_id=call_id,
            window=request_window,
            after=after,
            content=canonical({"content": args.content}),
            close_window=True,
        )
        data["windows"][request_window] = _default_window("tool_call")
        _sync_call(data, settings, call_id)
        after = [call_id]
    events[ident] = dict(
        kind="chat_message",
        window=args.window,
        after=after,
        content=args.content,
        chat={"speaker": args.speaker},
        call_id=call_id,
    )
    return [call_id, ident] if call_id else [ident]


def _view(source, name, arguments):
    if name == "view_event":
        event = source.events[arguments.event_id]
        try:
            visible = {
                window: [item["id"] for item in info["events"]]
                for window, info in causal_view(source, event.after).items()
            }
        except (ValueError, KeyError):
            visible = None
        return dict(event=event.model_dump(mode="json"), visible=visible)
    if name == "view_window":
        window = source.windows[arguments.window_id]
        return dict(
            window=window.model_dump(mode="json"),
            events={
                ident: event.model_dump(mode="json")
                for ident, event in source.events.items()
                if event.window == arguments.window_id
            },
        )
    return dict(
        windows={ident: window.model_dump(mode="json") for ident, window in source.windows.items()},
        events={
            ident: dict(
                kind=event.kind,
                window=event.window,
                after=event.after,
                close_window=event.close_window,
                **(
                    {"tool": event.tool, "call_id": event.call_id}
                    if event.kind == "tool_call"
                    else {}
                ),
            )
            for ident, event in source.events.items()
        },
    )


def apply(source, settings, name, arguments):
    """Return a new validated source and tool reply; schema errors leave the old source intact."""
    args = TOOLS[name][0].model_validate(arguments)
    if name == "view_diagnostics":
        return source, dict(diagnostics=findings(source, settings))
    if name in READ_ONLY:
        return source, _view(source, name, args)
    data = source.to_dict()
    events = data["events"]
    before = references(events)
    changes = args.model_dump(mode="json", exclude_unset=True)
    edited = []
    if name == "configure_window":
        ident = changes.pop("window_id")
        data["windows"][ident] = (
            data["windows"].get(ident, _default_window("external_text")) | changes
        )
    elif name == "post_tool_schema":
        _post_schema(data, settings, args.tool_ids)
        edited = [SCHEMA_ID]
    else:
        ident = changes.pop("event_id")
        if name in {"create_event", "post_chat_message"}:
            if ident in events:
                raise ValueError("Event ID already exists")
            if name == "post_chat_message":
                edited = _post_chat(data, settings, args)
            else:
                properties = changes.pop("window_properties", None)
                events[ident] = _merge_event({"kind": changes["kind"]}, changes)
                _ensure_windows(data, events[ident], properties)
                _sync_call(data, settings, ident)
                edited = [ident]
        elif name == "update_event":
            events[ident] = _merge_event(events[ident], changes)
            _ensure_windows(data, events[ident])
            _sync_call(data, settings, ident)
            edited = [ident]
        elif name == "delete_event":
            del events[ident]
    for window_id in before - references(events):
        data["windows"].pop(window_id, None)
    changed = GraphSource.model_validate(data)
    return changed, dict(event_ids=edited)
