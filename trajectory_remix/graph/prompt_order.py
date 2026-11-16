"""Serialize prompt bodies so consecutive requests share the longest possible prefix.

Inference servers with prefix (KV) caching reuse work only up to the first byte that
differs from an earlier request. Sorted-key JSON puts volatile fields such as
``correction`` and ``previous`` first and places a new event wherever its ID sorts, so
almost nothing was reused. Here top-level fields are written from most to least stable,
and trajectory events in causal order, so a new event is usually appended at the end.
The parsed JSON is identical to the sorted form; only byte order changes. Caching saves
time and cost, not context window: cached tokens still count toward the limit.
"""

import json

from .models import canonical


def ordered(pairs):
    """A JSON object whose top-level keys keep the given order; values stay canonical."""
    return "{" + ",".join(f"{json.dumps(key)}:{value}" for key, value in pairs) + "}"


def causal_order(events):
    """Event IDs in dependency order: each round takes every event whose dependencies are
    already listed, by ID. A new event depends on committed ones, so it lands late."""
    order, pending = [], set(events)
    while pending:
        ready = sorted(i for i in pending if not set(events[i].get("after", [])) & pending)
        ready = ready or sorted(pending)  # A broken graph still serializes deterministically.
        order.extend(ready)
        pending.difference_update(ready)
    return order


def source_json(source):
    """The prompt view of a source: rarely changing fields first, then events appended."""
    data = source.model_dump(mode="json", exclude={"metadata"})
    events = data.pop("events")
    pairs = [(key, canonical(value)) for key, value in sorted(data.items())]
    body = ordered((ident, canonical(events[ident])) for ident in causal_order(events))
    return ordered([*pairs, ("events", body)])
