"""The compact episode spec the assemble pass writes, and its conversion to a graph source.

A spec lists windows and events in causal order with short field names. The builder adds
what follows from them: the tool listing event, tool-call IDs on results, chat deliveries
(:echo events) and canonical JSON tool arguments. It never invents content.
"""

from . import tool_listing
from .flow import chat_echo
from .models import GraphSource, canonical

KINDS = dict(
    user="chat_message",
    document="external_text",
    think="agent_text",
    call="tool_call",
    result="tool_response",
)
LISTING = "tools"


def schema(tool_ids):
    """JSON schema for one spec. Tool IDs are limited to the catalogue."""
    window = dict(
        type="object",
        properties=dict(
            id=dict(type="string", pattern=r"^[A-Za-z0-9_.-]+$"),
            purpose=dict(type="string", pattern=r"^[A-Za-z0-9_.-]+$"),
            writable=dict(type="boolean"),
        ),
        required=["id", "purpose", "writable"],
        additionalProperties=False,
    )
    event = dict(
        type="object",
        properties=dict(
            id=dict(type="string", pattern=r"^[A-Za-z0-9_.-]+$"),
            kind=dict(type="string", enum=list(KINDS)),
            window=dict(type="string"),
            after=dict(type="array", items=dict(type="string")),
            content=dict(type="string"),
            arguments=dict(type="object"),
            tool=dict(type="string", enum=list(tool_ids)),
            call=dict(type="string"),
            speaker=dict(type="string"),
            close=dict(type="boolean"),
        ),
        required=["id", "kind", "window", "after"],
        additionalProperties=False,
    )
    return dict(
        type="object",
        properties=dict(
            tools=dict(type="array", items=dict(type="string", enum=list(tool_ids)), minItems=1),
            windows=dict(type="array", items=window, minItems=1),
            events=dict(type="array", items=event, minItems=2),
        ),
        required=["tools", "windows", "events"],
        additionalProperties=False,
    )


def build(spec, base, tools, extra_tools=0.5):
    """Return graph source data for spec on top of base (id, metadata).

    Raises ValueError with a correction an author can act on.
    """
    data = dict(base, windows={}, events={})
    listed = tool_listing.listing(tools, base["id"], LISTING, spec["tools"], extra_tools)
    data["metadata"] = dict(base.get("metadata", {}), shown_tools=listed["shown"])
    data["windows"][LISTING] = dict(purpose=tool_listing.WINDOW_PURPOSE, agent_writable=False)
    data["events"][LISTING] = dict(
        kind="external_text", window=LISTING, after=[], content=listed["content"]
    )
    for window in spec["windows"]:
        if window["id"] in data["windows"]:
            raise ValueError(f"Window {window['id']} is defined twice (or is the reserved tools)")
        data["windows"][window["id"]] = dict(
            purpose=window["purpose"], agent_writable=window["writable"]
        )
    for event in spec["events"]:
        _add(data, event, listed["shown"])
    return data


def _add(data, event, shown):
    ident = event["id"]
    if ident in data["events"]:
        raise ValueError(f"Event ID {ident} is used twice; give each event its own ID")
    missing = [a for a in event["after"] if a not in data["events"]]
    if missing:
        raise ValueError(
            f"Event {ident} follows {', '.join(missing)}, which is not an earlier event;"
            " list events in causal order and refer only to earlier IDs"
        )
    if event["window"] not in data["windows"]:
        raise ValueError(f"Event {ident} is in undefined window {event['window']}")
    built = dict(
        kind=KINDS[event["kind"]],
        window=event["window"],
        after=list(event["after"]) or [LISTING],
        content=event.get("content", ""),
    )
    if event.get("close"):
        built["close_window"] = True
    if event["kind"] == "user":
        built["chat"] = {"speaker": event.get("speaker") or "User"}
    if event["kind"] == "call":
        _call(built, event, shown)
    if event["kind"] == "result":
        built["call_id"] = event.get("call") or _answered(data, event)
    data["events"][ident] = built
    if event["kind"] == "call" and event["tool"] == "chat":
        _echo(data, ident)


def _call(built, event, shown):
    tool = event.get("tool")
    if not tool:
        raise ValueError(f"Call {event['id']} needs a tool")
    if tool not in shown:
        raise ValueError(f"Call {event['id']} uses {tool}, which is not in the chosen tools")
    built.update(tool=tool, call_id=event.get("call") or event["id"])
    built["content"] = canonical(event.get("arguments", {}))


def _answered(data, event):
    calls = [
        data["events"][a]["call_id"]
        for a in event["after"]
        if data["events"][a]["kind"] == "tool_call"
    ]
    if not calls:
        raise ValueError(
            f"Result {event['id']} must follow the call it answers, or name it in call"
        )
    return calls[0]


def _echo(data, ident):
    echo = chat_echo(GraphSource.model_validate(data), ident)
    if echo:
        body = echo.pop("content")
        data["events"][echo["id"]] = dict(echo["envelope"], content=body)
