"""Model prompts exclude storage snapshots and repeated evidence bodies."""

import json

import httpx
import pytest
from test_graph_generation import service

from trajectory_remix.graph import reviews
from trajectory_remix.graph.adaptive_flow import selection_payload, state
from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.provider import OpenAITransport, context_budget, response_contract


def initial(gen):
    job = gen.start([{"seed": 137}], {})
    gen._admit(job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    plans = gen.jobs.inspect(job)["config"]["config"]["plans"]
    return source, next(iter(plans.values()))


def test_real_initial_snapshot_excluded_and_proposal_preserved(tmp_path):
    gen, _ = service(tmp_path)
    source, plan = initial(gen)
    assert len(json.dumps(source.metadata)) > 60000
    payload = selection_payload(source, plan, state(plan))
    body = json.loads(payload["messages"][1]["content"])
    assert "metadata" not in body["source"]
    assert body["proposal"] == plan["proposal"]
    assert "sampling_provenance" in body["proposal"]
    wire, messages = response_contract(payload)
    payload["endpoint_config"] = (
        gen.settings_for(gen.jobs.listing()[0]["id"]).endpoints["g"].model_dump(mode="json")
    )
    budget = context_budget(payload, wire, messages)
    assert budget["input_bytes"] < budget["input_ceiling"]
    assert len(json.dumps(source.metadata)) > 60000  # Storage unchanged.


@pytest.mark.asyncio
async def test_review_frontiers_reference_bodies_once(tmp_path):
    from test_adaptive_flow import episode
    from test_graph_generation import finish

    gen, _ = service(tmp_path, episode(), fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    payload = reviews.payload(source, gen.settings_for(job), "fast", "review")
    body = json.loads(payload["messages"][1]["content"])
    assert "result-a" in body["causal_views"]["integrate"]["a"]
    assert "metadata" not in body["source"]
    assert source.events["result-a"].content not in json.dumps(body["causal_views"])


@pytest.mark.asyncio
async def test_oversized_prompt_stops_before_http_without_truncation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized request reached HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    payload = dict(
        operation="event",
        messages=[dict(role="user", content="x" * 17000)],
        schema={"type": "object"},
        endpoint_config=dict(
            url="http://127.0.0.1:1",
            model="local",
            context_window_tokens=16384,
            max_output_tokens=2048,
        ),
    )
    ticket = Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)
    result = await OpenAITransport()(ticket)
    assert result["context_limit"] and result["local_budget"]["input_bytes"] > 17000
    assert len(payload["messages"][0]["content"]) == 17000


def test_output_budget_must_leave_input_room():
    from trajectory_remix.graph.models import Endpoint

    with pytest.raises(ValueError):
        Endpoint(
            url="http://127.0.0.1:1",
            model="local",
            context_window_tokens=2048,
            max_output_tokens=2048,
        )


@pytest.mark.asyncio
async def test_event_writers_see_only_direct_ancestors(tmp_path):
    from test_adaptive_flow import episode
    from test_graph_generation import finish

    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source

    def ancestors(frontier):
        seen, todo = set(), list(frontier)
        while todo:
            ident = todo.pop()
            if ident not in seen:
                seen.add(ident)
                todo.extend(source.events[ident].after)
        return seen

    writes = [p for p in fake.calls if p["operation"] == "event"]
    assert writes
    for payload in writes:
        visible = json.loads(payload["messages"][1]["content"])["visible"]
        shown = {e["id"] for window in visible.values() for e in window["events"]}
        allowed = ancestors(payload["task"]["envelope"].get("after", []))
        assert shown <= allowed, (payload["task"]["id"], shown - allowed)
    # Parallel siblings never see each other.
    sibling = next(p for p in writes if p["task"]["id"] == "assign-b")
    assert "assign-a" not in sibling["messages"][1]["content"]
