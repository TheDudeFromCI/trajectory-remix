"""Entity-level diffs and three-way merges of trajectory sources.

A source splits into entities: the defaults, each window, each event and each metadata key.
`changes` lists what differs between two sources, entity by entity, and `merge` replays the
entities a proposal changed onto a newer source. Both sides changing the same entity
differently is a conflict; anything else merges cleanly.
"""

from __future__ import annotations

from .jobs import OwnershipConflict
from .models import canonical

KINDS = ("defaults", "window", "event", "metadata")
_SECTIONS = dict(window="windows", event="events", metadata="metadata")
_MISSING = object()


class ProposalConflict(OwnershipConflict):
    code = "proposal_conflict"

    def __init__(self, conflicts):
        names = ", ".join(label(kind, ident) for kind, ident in conflicts)
        super().__init__(f"Changed both here and in the proposal: {names}")
        self.conflicts = conflicts


def label(kind, ident):
    return "defaults" if kind == "defaults" else f"{kind} {ident}"


def entities(source: dict) -> dict:
    """{(kind, id): value} for every entity in a source dict."""
    found = {("defaults", None): source.get("defaults", {})}
    for kind, section in _SECTIONS.items():
        for ident, value in source.get(section, {}).items():
            found[(kind, ident)] = value
    return found


def _same(a, b):
    if a is _MISSING or b is _MISSING:
        return a is b
    return canonical(a) == canonical(b)


def _order(key):
    kind, ident = key
    return KINDS.index(kind), ident or ""


def changed_keys(base: dict, other: dict) -> list:
    old, new = entities(base), entities(other)
    keys = sorted(old.keys() | new.keys(), key=_order)
    return [k for k in keys if not _same(old.get(k, _MISSING), new.get(k, _MISSING))]


def changes(base: dict, proposed: dict) -> list[dict]:
    """What the proposal adds, removes or changes, with each entity before and after."""
    old, new = entities(base), entities(proposed)
    result = []
    for key in changed_keys(base, proposed):
        before, after = old.get(key), new.get(key)
        change = "added" if key not in old else "removed" if key not in new else "changed"
        result.append(dict(kind=key[0], id=key[1], change=change, before=before, after=after))
    return result


def conflicts(base: dict, current: dict, proposed: dict) -> list:
    """Entities changed since the base both in `current` and, differently, in `proposed`."""
    now, new = entities(current), entities(proposed)
    ours = set(changed_keys(base, current))
    return [
        key
        for key in changed_keys(base, proposed)
        if key in ours and not _same(now.get(key, _MISSING), new.get(key, _MISSING))
    ]


def merge(base: dict, current: dict, proposed: dict) -> dict:
    """`current` with the proposal's changes replayed on it; raises ProposalConflict."""
    clash = conflicts(base, current, proposed)
    if clash:
        raise ProposalConflict(clash)
    merged = entities(current)
    new = entities(proposed)
    for key in changed_keys(base, proposed):
        if key in new:
            merged[key] = new[key]
        else:
            merged.pop(key, None)
    return _assemble(current, merged)


def pick(base: dict, proposed: dict, keys) -> dict:
    """`proposed` with only the changes to `keys` kept; every other entity as in `base`.
    Raises ValueError for a key the proposal doesn't change."""
    keys, changed = set(keys), changed_keys(base, proposed)
    unknown = keys - set(changed)
    if unknown:
        names = ", ".join(label(kind, ident) for kind, ident in sorted(unknown, key=_order))
        raise ValueError(f"Not a change in the proposal: {names}")
    old, kept = entities(base), entities(proposed)
    for key in changed:
        if key in keys:
            continue
        if key in old:
            kept[key] = old[key]
        else:
            kept.pop(key, None)
    return _assemble(proposed, kept)


def _assemble(template: dict, found: dict) -> dict:
    source = dict(format=template["format"], id=template["id"])
    source["defaults"] = found.get(("defaults", None), {})
    for kind, section in _SECTIONS.items():
        source[section] = {ident: v for (k, ident), v in found.items() if k == kind}
    return source
