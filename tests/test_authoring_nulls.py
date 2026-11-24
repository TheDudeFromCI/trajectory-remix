"""Models often send every optional tool field, with unused ones as null."""

import pytest

from trajectory_remix.graph import authoring
from trajectory_remix.graph.authoring_tools import EventFields
from trajectory_remix.graph.models import GraphSource, ProjectSettings

ALL_NULL = {name: None for name in EventFields.model_fields if name != "event_id"}


def empty():
    return GraphSource.model_validate(
        dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")
    )


def test_create_event_ignores_null_optional_fields():
    args = ALL_NULL | dict(
        event_id="e1",
        kind="agent_text",
        window="window1",
        content="Something happens.",
        window_properties=None,
    )
    source, reply = authoring.apply(empty(), ProjectSettings(), "create_event", args)
    event = source.events["e1"]
    assert reply == {"event_ids": ["e1"]}
    assert (
        event.after == [] and event.close_window is False and event.content == "Something happens."
    )


def test_update_event_ignores_nulls_for_fields_of_other_kinds():
    settings = ProjectSettings()
    args = dict(event_id="e1", kind="agent_text", window="w", content="x")
    source, _ = authoring.apply(empty(), settings, "create_event", args)
    update = {k: None for k in ("tool", "call_id", "delivery", "chat")} | dict(
        event_id="e1", content="y"
    )
    source, _ = authoring.apply(source, settings, "update_event", update)
    assert source.events["e1"].content == "y"


def test_a_real_value_for_another_kind_is_still_refused_clearly():
    args = dict(event_id="e1", kind="agent_text", window="w", content="x", tool="chat")
    with pytest.raises(ValueError, match="do not apply to agent_text events: tool. Omit them"):
        authoring.apply(empty(), ProjectSettings(), "create_event", args)
