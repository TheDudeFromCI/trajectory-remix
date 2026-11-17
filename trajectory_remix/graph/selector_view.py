"""What the flow selector sees of the committed source: open windows in full, closed ones
as outlines.

Windows are meant to be short-lived: one opens, reasons, makes its tool calls and closes
once its conclusions are copied somewhere that stays open. A closed window's content is
gone for the agent, so the selector does not need it either. Its events keep their IDs,
kinds, windows and dependencies, so the episode's shape stays readable, but their content
is dropped. Without this, the selector's prompt grows with every event ever written and a
long episode runs out of context even when the agent's own view is small.

Closing a window changes earlier bytes of the prompt, so the next request misses the
prefix cache once. Later requests share the shorter prefix again.
"""

from .models import canonical
from .prompt_order import causal_order, compact, ordered


def closed_windows(source):
    """IDs of windows that some committed event has closed."""
    return {event.window for event in source.events.values() if event.close_window}


# Small selectors read a missing body as "not written yet" and write the event again, so
# the outline says plainly that it happened.
WRITTEN = "[Written. Its window is closed, so the text is gone.]"


def _outline(event):
    data = compact(event)
    data["content"] = WRITTEN
    return data


def source_view(source):
    """The prompt view of a source, with events in closed windows reduced to outlines."""
    closed = closed_windows(source)
    data = source.model_dump(mode="json", exclude={"metadata", "windows", "events"})
    windows = {}
    for key, window in source.windows.items():
        windows[key] = compact(window)
        if key in closed:
            windows[key]["closed"] = True
    events = {
        key: _outline(event) if event.window in closed else compact(event)
        for key, event in source.events.items()
    }
    pairs = [(key, canonical(value)) for key, value in sorted(data.items())]
    pairs.append(("windows", canonical(windows)))
    body = ordered((ident, canonical(events[ident])) for ident in causal_order(events))
    return ordered([*sorted(pairs), ("events", body)])


def open_windows(source):
    """Each open window's purpose, event count and content size, oldest first.

    A gauge for the selector: long-lived or bulky windows are the ones to wrap up and close.
    """
    closed = closed_windows(source)
    stats = {key: dict(purpose=w.purpose, events=0, chars=0) for key, w in source.windows.items()}
    for event in source.events.values():
        stats[event.window]["events"] += 1
        stats[event.window]["chars"] += len(event.content)
    return {key: value for key, value in stats.items() if key not in closed}
