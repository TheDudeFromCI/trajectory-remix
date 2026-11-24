"""Causal checks for event bodies shared by diagnostics and review signoff."""

from collections import defaultdict


def clears_body(event):
    return event.close_window or getattr(event, "replace_body", False)


def invalidators(source, ordered):
    by_window = defaultdict(list)
    for ident in ordered:
        event = source.events[ident]
        if clears_body(event):
            by_window[event.window].append(ident)
    return by_window


def evidence_blocker(source, ancestors, by_window, evidence_id, consumer_id, *, check_race=False):
    """Return the event and reason that hide, or could hide, an evidence body."""
    if evidence_id not in ancestors[consumer_id]:
        return evidence_id, "not a causal predecessor"
    evidence = source.events[evidence_id]
    if evidence.close_window:
        return evidence_id, "closes its own window"

    race = None
    for ident in by_window.get(evidence.window, ()):
        # A replacement's own content remains visible. A consumer's own write
        # takes effect after it reads the frontier.
        if ident == consumer_id or evidence_id not in ancestors[ident]:
            continue
        if ident in ancestors[consumer_id]:
            verb = "closes the window" if source.events[ident].close_window else "replaces its body"
            return ident, verb
        if check_race and consumer_id not in ancestors[ident] and race is None:
            race = (ident, "can clear the window before the consumer")
    return race
