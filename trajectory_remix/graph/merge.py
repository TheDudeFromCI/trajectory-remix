"""Three-way merge of JSON documents, used to rebase a stale save onto the latest revision.

A save names the revision it was edited from (the base). When another save landed in between,
the client's document (ours) and the stored one (theirs) both descend from that base. Objects
merge key by key; any other value (lists, strings, numbers) is replaced whole. A value both
sides changed differently is a conflict. Unless `resolutions` says which side to keep for it,
the merge raises MergeConflict listing every conflict, and nothing is merged.
"""

from __future__ import annotations

MISSING = object()
SIDES = ("ours", "theirs")


class MergeConflict(ValueError):
    """Both sides changed the same values in different ways.

    `conflicts` lists each one as {path, base, ours, theirs}; a side that removed the value
    leaves its key out."""

    def __init__(self, conflicts: list[dict]):
        first = "/".join(conflicts[0]["path"]) or "document root"
        more = f" and {len(conflicts) - 1} more" if len(conflicts) > 1 else ""
        super().__init__(f"Conflicting change at {first}{more}")
        self.conflicts = conflicts
        self.path = tuple(conflicts[0]["path"])


def merge(base, ours, theirs, resolutions: dict | None = None):
    """Return `ours` and `theirs` combined, or raise MergeConflict.

    `resolutions` maps a conflict path (a tuple of keys) to "ours" or "theirs"."""
    conflicts: list[dict] = []
    merged = _merge(base, ours, theirs, (), resolutions or {}, conflicts)
    if conflicts:
        raise MergeConflict(conflicts)
    return merged


def _merge(base, ours, theirs, path, resolutions, conflicts):
    if ours == theirs or theirs == base:
        return ours
    if ours == base:
        return theirs
    if all(isinstance(value, dict) for value in (base, ours, theirs)):
        merged = {}
        for key in [*theirs, *(k for k in ours if k not in theirs)]:
            value = _merge(
                base.get(key, MISSING),
                ours.get(key, MISSING),
                theirs.get(key, MISSING),
                (*path, key),
                resolutions,
                conflicts,
            )
            if value is not MISSING:
                merged[key] = value
        return merged
    keep = resolutions.get(path)
    if keep in SIDES:
        return ours if keep == "ours" else theirs
    sides = dict(base=base, ours=ours, theirs=theirs)
    conflicts.append(
        {"path": list(path)} | {name: v for name, v in sides.items() if v is not MISSING}
    )
    return MISSING
