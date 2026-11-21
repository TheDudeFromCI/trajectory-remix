"""Native repair keeps a single durable conversation through settlement."""

import json
from uuid import uuid4

import pytest
from test_graph_generation import finish, native_report, plan, service, tool_message


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


def patch():
    return tool_message(
        ("update_event", {"event_id": "x", "content": "repaired", "close_window": True})
    )


def verdict(status):
    return lambda t: tool_message(native_report(t.payload, ["x"], status))


async def run(gen, record):
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    return gen.store.read(record.source.id).source, gen.jobs.checkpoint(job, record.source.id)


@pytest.mark.asyncio
async def test_failing_evidence_keeps_the_session_open(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    fake.responses = [verdict("fail"), patch(), verdict("pass")]
    source, _ = await run(gen, record)
    assert source.metadata["stage"] == 1 and source.events["x"].content == "repaired"
    messages = fake.calls[1].payload["messages"]
    assert messages[:2] == fake.calls[0].payload["messages"]
    assert "unfeasible" in messages[-2]["content"]
    assert messages[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_unfeasible_signoff_is_failed_and_keeps_its_reason(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    reason = "The opening contradicts every later event; no spot edit can fix it."
    fake.responses = [
        verdict("fail"),
        tool_message(("signoff", {"status": "unfeasible", "summary": reason})),
    ]
    source, _ = await run(gen, record)
    outcome = source.metadata["repair_outcomes"][-1]
    assert source.metadata["stage"] == -3
    assert outcome["outcome"] == "unfeasible" and outcome["reason"] == reason


@pytest.mark.asyncio
async def test_views_show_saved_tool_edits(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    fake.responses = [patch(), tool_message(("view_event", {"event_id": "x"})), verdict("pass")]
    await run(gen, record)
    shown = fake.calls[2].payload["messages"][-1]
    assert shown["role"] == "tool"
    assert json.loads(shown["content"])["event"]["content"] == "repaired"


@pytest.mark.parametrize("reason", ["context_limit", "exchange_limit"])
@pytest.mark.asyncio
async def test_limits_preserve_partial_graph_and_history_without_starting_over(tmp_path, reason):
    ceiling = 1 if reason == "exchange_limit" else 10
    gen, fake = service(tmp_path, critical=False, max_authoring_exchanges=ceiling)
    record = detached(gen)
    fake.responses = [patch()]
    if reason == "context_limit":

        async def capped(ticket):
            if fake.calls:
                fake.calls.append(ticket)
                return {"context_limit": True}
            return await fake(ticket)

        gen.transport = capped
    source, checkpoint = await run(gen, record)
    assert source.metadata["stage"] == -3
    assert source.events["x"].content == "repaired"
    assert source.metadata["repair_outcomes"][-1]["outcome"] == reason
    assert checkpoint["authoring_session"]["exchanges"] == 1
    assert len(fake.calls) == (2 if reason == "context_limit" else 1)


def test_review_prompt_survives_an_event_in_an_unknown_window():
    from trajectory_remix.graph import repair_policy, reviews
    from trajectory_remix.graph.models import GraphSource

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
