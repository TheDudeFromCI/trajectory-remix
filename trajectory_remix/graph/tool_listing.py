"""The tool_schema listing: the tools an episode's agent is shown.

Assembly chooses the episode's tools; Remix then writes the listing itself: a masked
tool_schema event in a window whose purpose is "tools", holding the chosen tools'
schemas plus a random selection of other tools (each included with the project's
tool_schema_extra_chance, seeded by the trajectory and event IDs, so the same trajectory
always gets the same list). Manual authoring's tool checkboxes use the same listing.
"""

import hashlib
import json
import random
from copy import deepcopy

WINDOW_PURPOSE = "tools"


def extras(trajectory_id, event_id, candidates, probability):
    """Each candidate is added with the given probability; the same IDs give the same draw."""
    seed = hashlib.sha256(f"{trajectory_id}:{event_id}".encode()).hexdigest()
    draw = random.Random(int(seed[:16], 16))
    return [ident for ident in candidates if draw.random() < probability]


def render(tools, ids):
    """One tool per line: name, description and parameters, as the agent will read them."""
    lines = []
    for ident in ids:
        schema = deepcopy(tools[ident].get("input_schema", {}))
        text = schema.get("description") if isinstance(schema, dict) else None
        entry = {"name": ident, "description": text or tools[ident].get("name", ident)}
        entry["parameters"] = schema
        lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines)


def presented_ids(content):
    """Read tool declarations from typed events and legacy tools-window input."""
    ids = set()
    if isinstance(content, list):
        if any(piece.kind != "text" for piece in content):
            return ids
        content = "".join(piece.text for piece in content)
    for line in content.splitlines():
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("parameters"), (dict, bool))
        ):
            ids.add(entry["name"])
    return ids


def listing(tools, trajectory_id, event_id, chosen, chance):
    """The listing for one event: chosen tools always, others at the given chance.

    Shared by Assembly and manual authoring, so both produce identical events.
    Returns dict(chosen, added, shown, content).
    """
    catalogue = list(tools)
    chosen = list(dict.fromkeys(chosen))
    unknown = [ident for ident in chosen if ident not in tools]
    if unknown:
        raise ValueError("Unknown tool IDs: " + ", ".join(unknown) + "; use IDs from tools")
    others = [ident for ident in catalogue if ident not in chosen]
    added = extras(trajectory_id, event_id, others, chance)
    shown = [ident for ident in catalogue if ident in chosen or ident in added]
    return dict(chosen=chosen, added=added, shown=shown, content=render(tools, shown))
