"""The `size` search field: `m`, `=m`, `<=xs`, `>l`... compared by size-label order."""

import json
import re

from .size_labels import estimate_tokens, event_characters_json, nearest_index

OPERATORS = ("<=", ">=", "<", ">", "=")
_EXPRESSION = re.compile(r"^(<=|>=|<|>|=)?\s*(\S+)$")


def labels_of(store):
    return [x.model_dump() for x in store.settings().settings.size_labels]


def labels_for(store, query):
    """Size labels for a query that filters by size (None otherwise: no settings read)."""
    return labels_of(store) if isinstance(query, dict) and "size" in query else None


def parse(expression, labels):
    """Return (operator, rank) for a size expression, or raise ValueError."""
    match = _EXPRESSION.match(expression.strip()) if isinstance(expression, str) else None
    if not match:
        raise ValueError("Size search looks like m, <=xs or >l")
    names = [x["name"].lower() for x in labels]
    if match[2].lower() not in names:
        raise ValueError(
            f"Unknown size {match[2]!r}; sizes are {', '.join(x['name'] for x in labels)}"
        )
    return match[1] or "=", names.index(match[2].lower())


def clause(expression, labels):
    operator, rank = parse(expression, labels)
    totals = json.dumps([x["total_tokens"] for x in labels])
    return f"trx_size_rank(r.source,?){operator}?", [totals, rank]


def _rank(source_json, totals_json):
    return nearest_index(
        estimate_tokens(event_characters_json(source_json)), json.loads(totals_json)
    )


def register(db):
    db.create_function("trx_size_rank", 2, _rank, deterministic=True)
