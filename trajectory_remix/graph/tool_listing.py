"""The Show tools step: choose an episode's tools once, then list them for the agent.

A flow "tools" node asks the Generator, from a compact index (tool ID and description),
which tools the episode needs. Remix then writes the listing itself: a masked
external_text event in a window whose purpose is "tools", holding the chosen tools'
schemas plus a random selection of other tools (each included with the node's
extra_tools probability, seeded by the trajectory and event IDs, so the same trajectory
always gets the same list). The listing is placed before the opening event, so every
later event, and therefore the agent, sees it.

From then on, selector requests carry only the shown tools instead of the whole
catalogue, and a tool call to a tool that was not shown is rejected. Flows without a
tools node keep sending the whole catalogue, as before.
"""

import hashlib
import json
import random
from copy import deepcopy

from .models import GraphSource, canonical
from .prompt_order import ordered
from .validation import diagnose

OPERATION = "flow_tools"
WINDOW_PURPOSE = "tools"


def description(tool):
    schema = tool.get("input_schema")
    text = schema.get("description") if isinstance(schema, dict) else None
    return text or tool.get("name") or ""


def index(tools):
    """Tool ID to one-line description: enough to choose, far smaller than schemas."""
    return {ident: description(tool) for ident, tool in tools.items()}


def uses_listing(plan):
    return any(node["kind"] == "tools" for node in plan["flow"]["nodes"].values())


def selector_tools(plan, current):
    """What a selector request shows under "tools"."""
    if not uses_listing(plan):
        return plan["tools"]
    shown = current.get("shown_tools")
    if shown is None:
        return index(plan["tools"])
    return {ident: plan["tools"][ident] for ident in shown if ident in plan["tools"]}


def choice_schema(plan):
    ids = list(plan["tools"])
    return {
        "type": "object",
        "properties": {
            "tools": {
                "type": "array",
                "items": {"type": "string", "enum": ids},
                "minItems": 1,
                "description": "IDs of the tools this episode's agent will need.",
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "description": "Why these tools, in one or two sentences.",
            },
        },
        "required": ["tools", "reason"],
        "additionalProperties": False,
    }


def payload(source, plan, current, correction=""):
    node = plan["flow"]["nodes"][current["node"]]
    task = current.get("task") or {}
    upcoming = {key: task[key] for key in ("envelope", "instruction", "world") if key in task}
    return dict(
        operation=OPERATION,
        prompt_version="flow-tools/1",
        schema=choice_schema(plan),
        messages=[
            dict(role="system", content=node["instruction"]),
            dict(
                role="user",
                content=ordered(
                    [
                        ("tools", canonical(index(plan["tools"]))),
                        ("proposal", canonical(plan["proposal"])),
                        ("upcoming_event", canonical(upcoming)),
                        ("correction", canonical(correction)),
                    ]
                ),
            ),
        ],
    )


def extras(trajectory_id, event_id, candidates, probability):
    """Each candidate is added with the given probability; the same IDs give the same draw."""
    seed = hashlib.sha256(f"{trajectory_id}:{event_id}".encode()).hexdigest()
    draw = random.Random(int(seed[:16], 16))
    return [ident for ident in candidates if draw.random() < probability]


def render(tools, ids):
    """One tool per line: name, description and parameters, as the agent will read them."""
    lines = []
    for ident in ids:
        schema = deepcopy(tools[ident].get("input_schema") or {})
        text = schema.pop("description", None) if isinstance(schema, dict) else None
        entry = {"name": ident, "description": text or tools[ident].get("name", ident)}
        entry["parameters"] = schema
        lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines)


def _unused(taken, base):
    if base not in taken:
        return base
    number = 2
    while f"{base}_{number}" in taken:
        number += 1
    return f"{base}_{number}"


def accept(source, body, plan, settings, current):
    """Commit the listing event and remember the shown tools. Returns (source, notes)."""
    node = plan["flow"]["nodes"][current["node"]]
    catalogue = list(plan["tools"])
    chosen = list(dict.fromkeys(body["tools"]))
    unknown = [ident for ident in chosen if ident not in plan["tools"]]
    if unknown:
        raise ValueError("Unknown tool IDs: " + ", ".join(unknown) + "; use IDs from tools")
    event_id = _unused(source.events, "tools")
    others = [ident for ident in catalogue if ident not in chosen]
    added = extras(source.id, event_id, others, node.get("extra_tools", 0.5))
    shown = [ident for ident in catalogue if ident in chosen or ident in added]
    data = source.to_dict()
    window = next(
        (key for key, w in source.windows.items() if w.purpose == WINDOW_PURPOSE),
        None,
    )
    if window is None:
        window = _unused(source.windows, "tools")
        data["windows"][window] = dict(purpose=WINDOW_PURPOSE, agent_writable=False)
    data["events"][event_id] = dict(
        kind="external_text",
        window=window,
        after=[],
        content=render(plan["tools"], shown),
    )
    listed = GraphSource.model_validate(data)
    errors = [d for d in diagnose(listed, settings) if d.severity == "error"]
    if errors:
        raise ValueError(f"Tool listing would break the graph: {errors[0].message}")
    task = current.get("task")
    if task and task["id"] not in listed.events:
        # The next event (normally the opening) follows the listing, so all later events,
        # and the agent, can see it.
        after = task["envelope"].setdefault("after", [])
        task["envelope"]["after"] = [event_id, *[a for a in after if a != event_id]]
    previous = current.get("shown_tools") or []
    current["shown_tools"] = [i for i in catalogue if i in previous or i in shown]
    notes = dict(chosen=chosen, added=added, shown=shown, event=event_id, reason=body["reason"])
    return listed, notes


def check_tool_call(task, shown):
    """A tool call must use a tool the agent has been shown."""
    envelope = task.get("envelope", {})
    tool = envelope.get("tool")
    if shown is None or envelope.get("kind") not in {"tool_call", "tool_response"}:
        return
    if tool is not None and tool not in shown:
        raise ValueError(f"Tool {tool} was not shown to the agent; use one of: " + ", ".join(shown))
