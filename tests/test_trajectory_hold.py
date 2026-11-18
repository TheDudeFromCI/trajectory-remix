"""One troublesome trajectory is held for attention; the rest of the job keeps running."""

import pytest
from test_graph_generation import finish, plan, service

from trajectory_remix.graph import reviews


def picky(fake, broken):
    """Review output stays invalid for the first trajectory reviewed while broken is set."""
    chosen = {}

    async def call(t):
        if t.payload["operation"] == "review" and broken:
            chosen.setdefault("id", t.trajectory_id)
            if t.trajectory_id == chosen["id"]:
                fake.calls.append(t)
                return {"output": {"items": {}}, "model": "fixture"}
        return await fake(t)

    return call, chosen


def logs(gen, job):
    return " ".join(item["text"] for item in gen.jobs.logs(job)["items"])


@pytest.mark.asyncio
async def test_invalid_review_holds_one_trajectory_and_the_rest_finish(tmp_path):
    gen, fake = service(tmp_path, format_retries=0, critical=False)
    broken = [True]
    gen.transport, chosen = picky(fake, broken)
    job = gen.start([{"seed": 41}, {"seed": 42}], {"plan": plan()})
    await finish(gen, job)
    attempts = {a["trajectory_id"]: a for a in gen.jobs.attempts(job)}
    held = gen.store.read(chosen["id"]).source
    other = next(a for i, a in attempts.items() if i != chosen["id"])
    assert other["outcome"] == "pass" and attempts[chosen["id"]]["state"] == "active"
    assert "invalid" in held.metadata["needs_attention"]
    assert gen.jobs.inspect(job)["state"] == "paused"
    text = logs(gen, job)
    assert "held for attention" in text and "Only held trajectories remain (1)" in text

    broken.clear()  # The cause is fixed; Resume retries the held trajectory.
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert "needs_attention" not in gen.store.read(chosen["id"]).source.metadata


@pytest.mark.asyncio
async def test_local_error_preparing_one_trajectory_holds_only_it(tmp_path, monkeypatch):
    gen, fake = service(tmp_path, critical=False)
    original, chosen = reviews.payload, {}

    def failing(source, *args, **kwargs):
        chosen.setdefault("id", source.id)
        if source.id == chosen["id"]:
            raise KeyError("missing window")
        return original(source, *args, **kwargs)

    monkeypatch.setattr(reviews, "payload", failing)
    job = gen.start([{"seed": 43}, {"seed": 44}], {"plan": plan()})
    await finish(gen, job)
    outcomes = {a["trajectory_id"]: a["outcome"] for a in gen.jobs.attempts(job)}
    assert [o for i, o in outcomes.items() if i != chosen["id"]] == ["pass"]
    assert "local error" in gen.store.read(chosen["id"]).source.metadata["needs_attention"]
    assert gen.jobs.inspect(job)["state"] == "paused"


@pytest.mark.asyncio
async def test_a_held_trajectory_can_be_detached_to_finish_the_job(tmp_path):
    gen, fake = service(tmp_path, format_retries=0, critical=False)
    gen.transport, chosen = picky(fake, [True])
    job = gen.start([{"seed": 45}, {"seed": 46}], {"plan": plan()})
    await finish(gen, job)
    gen.jobs.detach(job, chosen["id"])
    assert gen.jobs.inspect(job)["state"] == "completed"
