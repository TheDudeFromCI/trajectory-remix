"""Random extra tools, added to tool_schema events when a trajectory is compiled.

A tool_schema event stores only the tools its author chose. The compiler lists those
tools plus, for each other project tool, a random extra (chance
``tool_schema_extra_chance``, seeded by the trajectory and event IDs so the same
trajectory always compiles the same listing). The stored source never contains extras.
"""

from copy import deepcopy

from . import tool_listing


def expand(source, settings):
    """Return ``(source, extras)``: the source with extras listed, and {event_id: [tool IDs]}.

    ``source`` is a plain mapping; it is copied only when an event gains extras.
    """
    if settings is None or not settings.tool_schema_extra_chance:
        return source, {}
    catalogue = {key: tool.model_dump(mode="json") for key, tool in settings.tools.items()}
    added = {}
    for event_id, event in source.get("events", {}).items():
        if event.get("kind") != "tool_schema" or not isinstance(event.get("content"), str):
            continue
        chosen = tool_listing.presented_ids(event["content"])
        others = [ident for ident in catalogue if ident not in chosen]
        extra = tool_listing.extras(
            source["id"], event_id, others, settings.tool_schema_extra_chance
        )
        if extra:
            added[event_id] = extra
    if not added:
        return source, {}
    source = deepcopy(source)
    for event_id, extra in added.items():
        event = source["events"][event_id]
        lines = [event["content"]] if event["content"] else []
        event["content"] = "\n".join([*lines, tool_listing.render(catalogue, extra)])
    return source, added
