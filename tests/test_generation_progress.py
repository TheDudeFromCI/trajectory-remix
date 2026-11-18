"""Progress is observable during requests without leaking model content."""

import asyncio

import pytest
from test_graph_generation import Fake, finish, service


def log_text(jobs, job):
    return "\n".join(row["text"] for row in jobs.logs(job, limit=1000)["items"])


@pytest.mark.asyncio
async def test_slow_first_request_logs_start_and_heartbeats_before_result(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    fake = Fake()

    async def delayed(ticket):
        entered.set()
        await release.wait()
        return await fake(ticket)

    gen, _ = service(tmp_path, delayed, fast=False)
    gen.jobs.heartbeat_interval = 0.01
    job = gen.start([{"seed": 137}], {})
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    gen._prepare(job, ident)
    running = asyncio.create_task(gen._call("g"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(200):
            if "Still waiting" in log_text(gen.jobs, job):
                break
            await asyncio.sleep(0.01)
        log = log_text(gen.jobs, job)
        assert "Request started" in log and "Still waiting" in log
        assert "operation=draft" in log and "prompt_bytes=" in log
        assert "Response received" not in log
        assert not gen.store.read(ident).source.events
    finally:
        release.set()
        await running
    assert "Response received" in log_text(gen.jobs, job)
    assert "Draft written" in log_text(gen.jobs, job)


@pytest.mark.asyncio
async def test_completed_generation_logs_events_reviews_and_settlement(tmp_path):
    gen, _ = service(tmp_path, fast=True, critical=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    log = log_text(gen.jobs, job)
    assert "Trajectory admitted" in log
    assert "Draft written" in log and "Graph assembled" in log and "events=" in log
    assert "entered review (fast)" in log and "fast review verdict: pass" in log
    assert "Trajectory settled" in log
    # Fixture model content must never be copied to the status log.
    assert "Visible observation" not in log and "Supported reply" not in log
    assert "learning_objective" not in log
