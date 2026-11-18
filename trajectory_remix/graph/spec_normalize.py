"""Settle harmless slips in an Assembly reply before it is checked and built.

In pilots most rejected assemblies broke one rule about chat deliveries: the model wrote its
own ":echo" events, or followed "<id>:echo" for an event that has no delivery (a person's
message, a non-chat tool call). Their meaning is clear, so they are fixed here rather than
sent back for a full rewrite. Anything ambiguous is left for the builder to explain.
"""

ECHO = ":echo"


def normalize(body):
    """Return the reply with unambiguous slips fixed. Never raises."""
    if not isinstance(body, dict):
        return body
    body = _unwrap(body)
    tools = body.get("tools")
    if isinstance(tools, dict):
        body["tools"] = list(tools)
    elif isinstance(tools, list):
        body["tools"] = [t["id"] if isinstance(t, dict) and "id" in t else t for t in tools]
    events = body.get("events")
    if isinstance(events, list):
        body["events"] = [e for e in events if not _written_echo(e)]
    return body


def _unwrap(body):
    """{"spec": {...}} or similar single wrappers around the spec."""
    if "events" in body or len(body) != 1:
        return body
    inner = next(iter(body.values()))
    return dict(inner) if isinstance(inner, dict) and "events" in inner else body


def _written_echo(event):
    return isinstance(event, dict) and str(event.get("id", "")).endswith(ECHO)


def resolve(after, events):
    """Map "<id>:echo" to "<id>" when <id> exists but has no delivery."""
    return [
        a[: -len(ECHO)] if a not in events and a.endswith(ECHO) and a[: -len(ECHO)] in events else a
        for a in after
    ]
