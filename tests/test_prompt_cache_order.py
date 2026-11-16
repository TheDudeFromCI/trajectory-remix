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
        "remaining_events",
        "previous",
        "correction",
    ]
    source = json.loads(body)["source"]
    assert list(source)[-1] == "events" and "metadata" not in source


@pytest.mark.asyncio
async def test_work_loop_requests_share_most_of_their_prefix(calls):
    nodes = [p["checkpoint"]["flow"]["node"] for p in calls if p["operation"] == "flow_select"]
    work = [wire(p) for p in calls if p["operation"] == "flow_select"]
    work = [text for node, text in zip(nodes, work) if node == "work"]
    assert len(work) >= 4
    for before, after in zip(work, work[1:]):
        # New events are appended, so only the newest event and the tail differ.
        assert shared(before, after) / len(after) > 0.8


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
    assert [m["content"] for m in messages][::2] == ["rules", "data"]
    assert "Schema:" in messages[1]["content"]
