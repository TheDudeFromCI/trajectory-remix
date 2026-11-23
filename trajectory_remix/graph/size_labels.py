"""Trajectory size labels: character counts, estimated tokens and nearest-size lookup.

Source-only and tokenizer-free. Token counts are estimated from characters, so a label is a
coarse size class, never a measured export size.
"""

import json
import math

CHARS_PER_TOKEN = 3.5
AGENT_KINDS = ("agent_text", "tool_call")

# (name, agent tokens, total tokens); sizes double at each step.
DEFAULT_SIZE_LABELS = tuple(
    (name, int(agent * 1024), int(total * 1024))
    for name, agent, total in (
        ("XXXS", 0.25, 4),
        ("XXS", 0.5, 8),
        ("XS", 1, 16),
        ("S", 2, 32),
        ("M", 4, 64),
        ("L", 8, 128),
        ("XL", 16, 256),
        ("XXL", 32, 512),
        ("XXXL", 64, 1024),
    )
)


def default_size_labels():
    return [dict(name=n, agent_tokens=a, total_tokens=t) for n, a, t in DEFAULT_SIZE_LABELS]


def event_characters(event):
    pieces = event.content if isinstance(event.content, list) else [event.content]
    return sum(
        len(p if isinstance(p, str) else p.text)
        for p in pieces
        if isinstance(p, str) or getattr(p, "kind", None) == "text"
    )


def total_characters(source):
    return sum(event_characters(e) for e in source.events.values())


def agent_characters(source):
    return sum(event_characters(e) for e in source.events.values() if e.kind in AGENT_KINDS)


def estimate_tokens(characters):
    return math.ceil(characters / CHARS_PER_TOKEN)


def nearest_size(total_tokens, labels):
    """Name of the label whose total-token size is nearest in log2 space (None if no labels)."""
    if not labels:
        return None
    if total_tokens < 1:
        return min(labels, key=lambda x: x["total_tokens"])["name"]
    here = math.log2(total_tokens)
    return min(labels, key=lambda x: abs(math.log2(x["total_tokens"]) - here))["name"]


def nearest_index(total_tokens, totals):
    """Index of the nearest size in `totals` (sorted ascending) by log2 distance."""
    if total_tokens < 1:
        return 0
    here = math.log2(total_tokens)
    return min(range(len(totals)), key=lambda i: abs(math.log2(totals[i]) - here))


def event_characters_json(source_json):
    """Total characters of a serialized source, without building models."""
    count = 0
    for event in json.loads(source_json).get("events", {}).values():
        content = event.get("content", "")
        for piece in content if isinstance(content, list) else [content]:
            count += len(piece if isinstance(piece, str) else piece.get("text", ""))
    return count


def size_of(source, labels):
    total, agent = total_characters(source), agent_characters(source)
    return dict(
        total_characters=total,
        agent_characters=agent,
        total_tokens_estimate=estimate_tokens(total),
        agent_tokens_estimate=estimate_tokens(agent),
        label=nearest_size(estimate_tokens(total), labels),
    )
