"""Repair fails only when the repairer gives up, or at the round cap."""

from uuid import uuid4

import pytest
from test_graph_generation import finish, plan, report, service


def detached(gen):
    return gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan()["windows"],
            events={"x": dict(kind="agent_text", window="reason", content="before")},
            metadata={"stage": -1},
        )
    )


def patch(t):
    return {
        "action": "patch",
        "event_id": "x",
        "expected_source_hash": t.payload["source_hash"],
        "patch": {"content": "repaired"},
    }


def verdict(status):
    return lambda t: {"action": "verdict", "review": report(t.payload, status, ["x"])}


async def run(gen, record):
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    return gen.store.read(record.source.id).source


@pytest.mark.asyncio
async def test_failing_verdict_keeps_the_session_open(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    fake.responses = [verdict("fail"), patch, verdict("pass")]
    source = await run(gen, record)
    assert source.metadata["stage"] == 1 and source.events["x"].content == "repaired"
    told = fake.calls[1].payload["messages"][-1]["content"]
    assert "still_failing" in told and "give_up" in told


@pytest.mark.asyncio
async def test_give_up_is_the_failure_and_keeps_its_reason(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    reason = "The opening contradicts every later event; no spot patch can fix it."
    fake.responses = [verdict("fail"), {"action": "give_up", "reason": reason}]
    source = await run(gen, record)
    outcome = source.metadata["repair_outcomes"][-1]
    assert source.metadata["stage"] == -3
    assert outcome["outcome"] == "unfeasible" and outcome["reason"] == reason


@pytest.mark.asyncio
async def test_view_returns_the_current_source(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    fake.responses = [patch, {"action": "view"}, verdict("pass")]
    await run(gen, record)
    shown = fake.calls[2].payload["messages"][-1]["content"]
    assert '"repaired"' in shown and "diagnostics" in shown


def limited(fake, at):
    original = fake.__call__

    async def call(t):
        if len(fake.calls) in at:
            fake.calls.append(t)
            return {"context_limit": True}
        return await original(t)

    return call


@pytest.mark.asyncio
async def test_context_limit_starts_a_fresh_round_on_the_current_graph(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    fake.responses = [patch, verdict("pass")]
    gen.transport = limited(fake, {1})
    source = await run(gen, record)
    assert source.metadata["stage"] == 1
    fresh = fake.calls[2].payload["messages"]
    assert '"repaired"' in fresh[1]["content"]  # The new round starts from the patched graph.


@pytest.mark.asyncio
async def test_round_cap_is_the_only_other_failure(tmp_path):
    gen, fake = service(tmp_path, critical=False, repair_rounds=1)
    record = detached(gen)
    fake.responses = [patch]
    gen.transport = limited(fake, {1})
    source = await run(gen, record)
    assert source.metadata["stage"] == -3
    assert source.metadata["repair_outcomes"][-1]["outcome"] == "round_limit"


def test_review_prompt_survives_an_event_in_an_unknown_window():
    from trajectory_remix.graph import repair_policy, reviews
    from trajectory_remix.graph.models import GraphSource

    # A repair patch can add an event in a window that does not exist yet.
    source = GraphSource.model_validate(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan()["windows"],
            events={
                "x": dict(kind="agent_text", window="reason", content="before"),
                "y": dict(kind="agent_text", window="nowhere", after=["x"], content="new"),
                "z": dict(kind="agent_text", window="reason", after=["y"], content="later"),
            },
        )
    )
    assert "unavailable" in reviews.causal_evidence(source)["z"]
    assert isinstance(repair_policy.inspect(source, "z")["visible"], str)
