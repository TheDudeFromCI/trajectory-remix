"""Three-way merge of JSON documents, used to rebase a stale save onto the latest revision.

A save names the revision it was edited from (the base). When another save landed in between,
the client's document (ours) and the stored one (theirs) both descend from that base. Objects
merge key by key; any other value (lists, strings, numbers) is replaced whole. A value both
sides changed differently is a conflict, and nothing is merged.
"""

from __future__ import annotations

MISSING = object()


class MergeConflict(ValueError):
    """Both sides changed the same value in different ways."""

    def __init__(self, path: tuple):
        super().__init__("Conflicting change at " + ("/".join(map(str, path)) or "document root"))
        self.path = path


def merge(base, ours, theirs, path: tuple = ()):
    """Return `ours` and `theirs` combined, or raise MergeConflict."""
    if ours == theirs or theirs == base:
        return ours
    if ours == base:
        return theirs
    if all(isinstance(value, dict) for value in (base, ours, theirs)):
        merged = {}
        for key in [*theirs, *(k for k in ours if k not in theirs)]:
            value = merge(
                base.get(key, MISSING),
                ours.get(key, MISSING),
                theirs.get(key, MISSING),
                (*path, key),
            )
            if value is not MISSING:
                merged[key] = value
        return merged
    raise MergeConflict(path)
