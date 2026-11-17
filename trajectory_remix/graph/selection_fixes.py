"""Settle details of a selection that small selectors get wrong but the backend knows.

- A complete call to a catalogue tool is always written as JSON arguments. Offered a
  choice, selectors picked "text", and the writer then wrote the message itself
  ("Hi Alex, ...") instead of the tool's arguments, so chat never reached the room.
  Text stays for call fragments, which need not be valid JSON on their own.
- A new window's reasoning level must be one of the project's. A level that matches one
  but for case is corrected; an invented one ("light", "moderate") is dropped rather than
  failing the step, since the window works the same without it.
- An event follows the latest event in its window, and a result without a call_id answers
  the call it follows. Both are mechanical, and selectors often leave them out.
"""


def argument_mode(envelope, tools, chosen):
    """ "json" for a complete call to a known tool, otherwise what the selector chose."""
    complete = envelope.get("insert_tool_begin", True) and envelope.get("insert_tool_end", True)
    if envelope.get("kind") == "tool_call" and envelope.get("tool") in tools and complete:
        return "json"
    return chosen


def reasoning_level(value, levels):
    """The project's spelling of a level, or None when it names none of them."""
    if value is None or value in levels:
        return value
    return next((level for level in levels if level.lower() == str(value).lower()), None)


def _fresh(windows, base):
    n = 2
    while f"{base}_{n}" in windows:
        n += 1
    return f"{base}_{n}"


def new_windows(source, requested, envelope, closed):
    """The windows to add, with a correction that says what to do instead when needed.

    Repeating an open window exactly as it is defined is harmless and is ignored.
    """
    target = envelope.get("window")
    if target in closed:
        raise ValueError(
            f"Window {target} is closed and can never be written again. Put this event in a"
            f" NEW window, for example {_fresh(source.windows, target)}, defined in windows."
        )
    added = {}
    for key, definition in requested.items():
        current = source.windows.get(key)
        if current is None:
            added[key] = definition
            continue
        same = all(getattr(current, field) == value for field, value in definition.items())
        if key in closed or not same:
            raise ValueError(
                f"Window {key} already exists; leave it out of windows to use it as it is,"
                f" or define a NEW window such as {_fresh(source.windows, key)}."
            )
    return added


def _ancestors(source, ids):
    seen, todo = set(), list(ids)
    while todo:
        ident = todo.pop()
        if ident not in seen and ident in source.events:
            seen.add(ident)
            todo.extend(source.events[ident].after)
    return seen


def follow_window(source, envelope):
    """Make the event follow the latest event already in its window.

    Writers in one window are sequential, and selectors often leave the link out. The
    latest event is the one no other event in the window comes after.
    """
    window = envelope.get("window")
    mine = [i for i, e in source.events.items() if e.window == window]
    if not mine:
        return
    earlier = set().union(*(_ancestors(source, source.events[i].after) for i in mine))
    latest = [i for i in mine if i not in earlier]
    seen = _ancestors(source, envelope["after"])
    envelope["after"] += [i for i in latest if i not in seen]


def response_call(source, envelope):
    """A result without a call_id answers the one call it follows, if there is one."""
    if envelope.get("kind") != "tool_response" or envelope.get("call_id"):
        return
    calls = [
        source.events[i].call_id
        for i in envelope["after"]
        if i in source.events and source.events[i].kind == "tool_call"
    ]
    if len(set(calls)) == 1:
        envelope["call_id"] = calls[0]
