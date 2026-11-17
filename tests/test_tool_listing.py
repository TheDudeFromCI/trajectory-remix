"""Show tools: choose an episode's tools once, list them for the agent, send only those."""

import copy
import json

import pytest
import yaml
from flow_fixtures import MINIMAL_FLOW
from test_adaptive_flow import episode
from test_graph_generation import finish, service

from trajectory_remix.graph import tool_listing
from trajectory_remix.graph.adaptive_flow import accept_selection, load_flow
from trajectory_remix.graph.models import GraphSource


async def run_default(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    return fake, source


@pytest.mark.asyncio
async def test_listing_comes_before_the_opening_so_the_agent_sees_it(tmp_path):
    fake, source = await run_default(tmp_path)
    listing = source.events["tools"]
    assert listing.kind == "external_text" and listing.supervision.masked
    assert source.windows[listing.window].purpose == "tools"
    assert not source.windows[listing.window].agent_writable
    assert source.events["opening"].after == ["tools"]
    lines = [json.loads(line) for line in listing.content.splitlines()]
    assert {line["name"] for line in lines} == {"chat", "read"}
    assert all({"name", "description", "parameters"} == set(line) for line in lines)
    # The agent's own prompts (causal ancestors) include the listing.
    think = next(p for p in fake.calls if p.get("task", {}).get("id") == "think")
    assert (
        '"name":"chat"'
        in json.loads(think["messages"][1]["content"])["visible"]["tools"]["events"][0]["content"]
    )


@pytest.mark.asyncio
async def test_selectors_get_an_index_first_and_only_shown_schemas_after(tmp_path):
    fake, _ = await run_default(tmp_path)
    selects = [
        json.loads(p["messages"][1]["content"])
        for p in fake.calls
        if p["operation"] == "flow_select"
    ]
    first, later = selects[0]["tools"], selects[1]["tools"]
    assert all(isinstance(value, str) for value in first.values())  # Descriptions only.
    assert set(later) == {"chat", "read"} and "input_schema" in later["chat"]
    choice = next(p for p in fake.calls if p["operation"] == "flow_tools")
    assert choice["schema"]["properties"]["tools"]["items"]["enum"] == ["chat", "read"]
    assert "upcoming_event" in json.loads(choice["messages"][1]["content"])


def test_random_additions_are_repeatable_and_keep_chosen_tools():
    candidates = [f"t{i}" for i in range(40)]
    once = tool_listing.extras("trajectory-a", "tools", candidates, 0.5)
    assert once == tool_listing.extras("trajectory-a", "tools", candidates, 0.5)
    assert once != tool_listing.extras("trajectory-b", "tools", candidates, 0.5)
    assert 8 < len(once) < 32  # About half.
    assert tool_listing.extras("t", "tools", candidates, 0) == []
    assert tool_listing.extras("t", "tools", candidates, 1) == candidates


def plan_with(tools, extra=0.0):
    flow = load_flow()["flow"]
    flow["nodes"]["choose_tools"]["extra_tools"] = extra
    plan = dict(flow=flow, tools=tools, proposal={}, windows={})
    return plan


def source():
    return GraphSource.model_validate(
        dict(format="kidra-trajectory-graph/1", id="00000000-0000-4000-8000-000000000000")
    )


def test_only_chosen_tools_without_random_additions_and_unshown_calls_are_refused(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    tools = {k: v.model_dump(mode="json") for k, v in settings.tools.items()}
    plan = plan_with(tools)
    current = dict(
        node="choose_tools",
        task=dict(id="opening", envelope=dict(kind="chat_message", window="room", after=[])),
    )
    listed, notes = tool_listing.accept(
        source(), {"tools": ["chat"], "reason": "talk"}, plan, settings, current
    )
    assert notes["shown"] == ["chat"] and current["shown_tools"] == ["chat"]
    assert current["task"]["envelope"]["after"] == ["tools"]
    with pytest.raises(ValueError, match="Unknown tool IDs: web"):
        tool_listing.accept(
            source(), {"tools": ["web"], "reason": "x"}, plan, settings, dict(current)
        )
    selection = dict(
        finished=False,
        reason="read it",
        windows={"call": dict(purpose="tool", agent_writable=True)},
        task=dict(
            id="go",
            envelope=dict(
                kind="tool_call", window="call", after=["tools"], tool="read", call_id="c"
            ),
        ),
    )
    with pytest.raises(ValueError, match="Tool read was not shown to the agent; use one of: chat"):
        accept_selection(listed, selection, plan, settings, shown=current["shown_tools"])


@pytest.mark.asyncio
async def test_flows_without_a_tools_node_still_send_the_whole_catalogue(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {"flow_yaml": yaml.safe_dump(copy.deepcopy(MINIMAL_FLOW))})
    await finish(gen, job)
    first = json.loads(
        next(p for p in fake.calls if p["operation"] == "flow_select")["messages"][1]["content"]
    )
    assert "input_schema" in first["tools"]["chat"]
    assert not any(p["operation"] == "flow_tools" for p in fake.calls)


def test_manual_listing_endpoint_matches_the_flow_step(tmp_path):
    from fastapi.testclient import TestClient

    from trajectory_remix.graph.studio_api import create_studio_app

    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    tools = {k: v.model_dump(mode="json") for k, v in settings.tools.items()}
    flow_built = tool_listing.listing(tools, "traj-1", "tools", ["read"], 0.5)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        body = dict(trajectory_id="traj-1", event_id="tools", tools=["read"], extra_tools=0.5)
        served = client.post("/api/v1/tools/listing", json=body).json()
        assert served == flow_built  # Same trajectory and event: the same listing.
        none = client.post("/api/v1/tools/listing", json=dict(body, extra_tools=0)).json()
        assert none["shown"] == ["read"] and none["added"] == []
        bad = client.post("/api/v1/tools/listing", json=dict(body, tools=["nope"]))
        assert bad.status_code == 422 and "Unknown tool IDs: nope" in bad.json()["message"]
