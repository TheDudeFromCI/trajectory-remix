"""The tool_schema event kind, the project's random-tool chance and the listing route default."""

import json

import pytest
from test_graph_compiler import compile_source, event, source
from test_graph_generation import service, spec

from trajectory_remix.graph import compact_spec
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


def test_builder_records_extras_and_chance_zero_disables_them():
    tools = {n: ToolDefinition(name=n).model_dump(mode="json") for n in ("chat", "read", "a", "b")}
    base = dict(format="kidra-trajectory-graph/1", id="7d3f8a2e-1b4c-4e5f-9a6b-0c1d2e3f4a5b")
    none = compact_spec.build(spec(), base, tools, 0)["events"]["tools"]
    every = compact_spec.build(spec(), base, tools, 1)["events"]["tools"]
    assert none["kind"] == every["kind"] == "tool_schema"
    assert none["extra_tools"] == []
    assert set(every["extra_tools"]) == {"a", "b"} - set(spec()["tools"])


def test_listing_route_uses_the_project_chance_when_none_is_sent(tmp_path):
    from fastapi.testclient import TestClient

    from trajectory_remix.graph.studio_api import create_studio_app

    gen, _ = service(tmp_path)
    store = gen.store
    record = store.settings()
    settings = record.settings
    for name in ("a", "b", "c"):
        settings.tools[name] = ToolDefinition(name=name)
    settings.tool_schema_extra_chance = 1
    store.update_settings(settings, record.revision)
    app = create_studio_app(store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        body = dict(trajectory_id="t", event_id="tools", tools=[])
        served = client.post("/api/v1/tools/listing", json=body).json()
        assert set(served["added"]) == set(settings.tools)  # Nothing chosen; chance 1 adds all.
        explicit = client.post("/api/v1/tools/listing", json=dict(body, extra_tools=0)).json()
        assert explicit["shown"] == [] and explicit["content"] == ""
