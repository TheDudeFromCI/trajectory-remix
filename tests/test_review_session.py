"""A review is one editable native session: it repairs what fails, then signs off."""

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
    """A detached Unsuccessful trajectory is reviewed again at Fast and fixed there."""
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
    (evaluation,) = source.metadata["evaluations"]
    assert source.metadata["stage"] == -1, "an unfeasible review fails its tier"
    assert evaluation["verdict"] == "fail" and evaluation["summary"] == reason


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
async def test_limits_keep_edits_and_the_stage_without_a_verdict(tmp_path, reason):
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
    assert source.metadata["stage"] == -1 and "evaluations" not in source.metadata
    assert source.events["x"].content == "repaired"
    assert source.metadata["review_outcomes"][-1]["outcome"] == reason
    assert checkpoint["authoring_session"]["exchanges"] == 1
    assert len(fake.calls) == (2 if reason == "context_limit" else 1)


def test_settings_with_the_retired_repair_options_still_load():
    from trajectory_remix.graph.models import GenerationOptions

    options = GenerationOptions.model_validate(
        {"repair": False, "repair_rounds": 3, "repair_context_bytes": 4096}
    )
    assert options.assistant_context_bytes == 4096
    assert {"repair", "repair_rounds", "repair_context_bytes"}.isdisjoint(options.model_dump())


@pytest.mark.asyncio
async def test_a_review_queued_for_the_retired_pipeline_pauses(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = detached(gen)
    snapshot = gen.store.settings()
    options = dict(
        mode="standard",
        repair=True,
        settings=snapshot.settings.model_dump(mode="json"),
        settings_revision=snapshot.revision,
        driver="draft-assembly/2",
    )
    with gen.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        config = dict(query={}, action="review_repair", options=options)
        job = gen.jobs._create(db, "bulk", config, 1)
        db.execute(
            "INSERT INTO graph_bulk_items VALUES(?,0,?,?,'pending',NULL,NULL)",
            (job, record.source.id, record.revision),
        )
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "paused" and not fake.calls
    logs = [line["text"] for line in gen.jobs.logs(job)["items"]]
    assert any("retired review pipeline" in line for line in logs)
