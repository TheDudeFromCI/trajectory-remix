"""Prompts are ordered for prefix caching: stable fields first, new events appended."""

import json

import pytest
from test_adaptive_flow import episode
from test_graph_generation import finish, service

from trajectory_remix.graph.provider import response_contract


def keys(text):
    return list(json.loads(text, object_pairs_hook=lambda pairs: [k for k, _ in pairs]))


def wire(payload):
    return "".join(m["role"] + m["content"] for m in response_contract(payload)[1])


def shared(a, b):
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


@pytest.fixture
async def calls(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    await finish(gen, gen.start([{"seed": 137}], {}))
    return fake.calls


@pytest.mark.asyncio
async def test_selector_body_runs_from_stable_to_volatile_with_same_content(calls):
    select = next(p for p in calls if p["operation"] == "flow_select")
    body = select["messages"][1]["content"]
    assert keys(body) == [
        "tools",
        "proposal",
        "source",
        "current_step",
        "remaining_events",
        "previous",
        "correction",
    ]
    source = json.loads(body)["source"]
    assert list(source)[-1] == "events" and "metadata" not in source


@pytest.mark.asyncio
async def test_work_loop_requests_reuse_everything_before_the_newest_event(calls):
    selects = [p for p in calls if p["operation"] == "flow_select"]
    work = [p for p in selects if p["checkpoint"]["flow"]["node"] == "work"]
    assert len(work) >= 4
    step = json.loads(work[0]["messages"][1]["content"])["current_step"]["instruction"]
    for before, after in zip(work, work[1:]):
        new = set(json.loads(after["messages"][1]["content"])["source"]["events"])
        new -= set(json.loads(before["messages"][1]["content"])["source"]["events"])
        a, b = wire(before), wire(after)
        uncached = len(b) - shared(a, b)
        # Only the new event, the step instruction kept last for small models, and the
        # short tail (remaining_events, previous notes, correction) are new bytes.
        newest = sum(
            len(json.dumps(json.loads(after["messages"][1]["content"])["source"]["events"][i]))
            for i in new
        )
        assert uncached < newest + len(json.dumps(step)) + 1500, (uncached, newest)
        assert b.index('"current_step"') > b.index('"events"')


def test_unchanged_flows_keep_their_node_instruction_as_system_prompt():
    from trajectory_remix.graph.adaptive_flow import selection_payload
    from trajectory_remix.graph.models import GraphSource

    plan = dict(
        flow=dict(max_events=8, nodes={"pick": dict(kind="select", instruction="ONLY ME")}),
        tools={},
        proposal={},
    )
    source = GraphSource.model_validate(
        dict(format="kidra-trajectory-graph/1", id="00000000-0000-4000-8000-000000000000")
    )
    payload = selection_payload(source, plan, dict(node="pick", output=None))
    assert payload["messages"][0]["content"] == "ONLY ME"
    assert "current_step" not in json.loads(payload["messages"][1]["content"])


@pytest.mark.asyncio
async def test_event_body_puts_contract_before_visible_context(calls):
    call = next(p for p in calls if p.get("task", {}).get("envelope", {}).get("tool") == "read")
    order = keys(call["messages"][1]["content"])
    assert order[:4] == ["tool", "input_schema", "kind", "task"]
    assert order.index("visible") > order.index("task") and order[-1] == "correction"


def test_json_mode_schema_follows_the_system_prompt():
    payload = dict(
        operation="flow_select",
        schema={"type": "object", "properties": {"x": {}}},
        messages=[dict(role="system", content="rules"), dict(role="user", content="data")],
    )
    mode, messages = response_contract(payload)
    assert mode == {"type": "json_object"}
    assert [m["role"] for m in messages] == ["system", "user"]  # Roles alternate.
    assert messages[1]["content"].startswith("Return one JSON object")
    assert messages[1]["content"].endswith(
        "Schema: " + '{"type": "object", "properties": {"x": {}}}' + "\n\ndata"
    )
    assert payload["messages"][1]["content"] == "data"  # The payload itself is unchanged.
