"""Tool declarations visible at a source event's causal frontier."""

from .tool_listing import WINDOW_PURPOSE, presented_ids


def presentations(source, ordered):
    windows = {
        ident for ident, window in source.windows.items() if window.purpose == WINDOW_PURPOSE
    }
    result = []
    for ident in ordered:
        event = source.events[ident]
        if event.window in windows:
            names = (
                presented_ids(event.content)
                if event.kind in ("tool_schema", "external_text")
                else set()
            )
            result.append((ident, event, names))
    return result


def visible_at(rows, ancestors):
    windows = {}
    for ident, event, names in rows:
        if ident not in ancestors:
            continue
        if event.close_window or getattr(event, "replace_body", False):
            windows.pop(event.window, None)
        if names and not event.close_window:
            windows.setdefault(event.window, []).append((ident, names))
    visible = [entry for window in windows.values() for entry in window]
    return {name for _, names in visible for name in names}, sorted(ident for ident, _ in visible)
