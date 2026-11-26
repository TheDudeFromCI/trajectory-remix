"""The tool_schema event kind, the project's random-tool chance and the listing route default."""

import json

import pytest
from test_graph_compiler import compile_source, event, source

from trajectory_remix.graph.models import GraphSource, ProjectSettings, ToolDefinition


def test_tool_schema_compiles_like_masked_external_input():
    windows = dict(
        tools=dict(purpose="tools", agent_writable=False),
        work=dict(purpose="reasoning", agent_writable=True),
    )
    compiled = {}
    for kind in ("external_text", "tool_schema"):
        s = source(
            {
                "list": event(kind, "tools", '{"name":"read"}', supervision={"masked": True}),
                "think": event("agent_text", "work", "ok", ["list"]),
            },
            windows,
        )
        _, arrays, _ = compile_source(s)
        compiled[kind] = json.dumps(arrays, default=lambda v: v.tolist(), sort_keys=True)
    assert compiled["tool_schema"] == compiled["external_text"]


def test_tool_schema_defaults_and_extra_tools_round_trip():
    s = source({"list": event("tool_schema", "tools", "x", extra_tools=["web"])})
    stored = s.events["list"]
    assert stored.supervision.masked is True and stored.extra_tools == ["web"]
    assert GraphSource.model_validate(s.to_dict()).events["list"].extra_tools == ["web"]


def test_project_chance_defaults_to_half_and_rejects_out_of_range():
    assert ProjectSettings().tool_schema_extra_chance == 0.5
    with pytest.raises(ValueError):
        ProjectSettings(tool_schema_extra_chance=1.5)


def test_extras_join_the_listing_at_compile_time_only():
    from trajectory_remix.graph import tool_extras

    settings = ProjectSettings(tool_schema_extra_chance=1)
    for name in ("a", "b", "c"):
        settings.tools[name] = ToolDefinition(name=name)
    data = source({"list": event("tool_schema", "tools", '{"name":"a","parameters":{}}')}).to_dict()
    expanded, added = tool_extras.expand(data, settings)
    assert set(added["list"]) == set(settings.tools) - {"a"}
    assert expanded["events"]["list"]["content"].count("\n") == len(settings.tools) - 1
    assert (
        data["events"]["list"]["content"] == '{"name":"a","parameters":{}}'
    )  # Stored source unchanged.
    settings.tool_schema_extra_chance = 0
    assert tool_extras.expand(data, settings) == (data, {})
    assert tool_extras.expand(data, None) == (data, {})
