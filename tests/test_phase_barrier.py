"""Per-job phase barriers: wait before Assembly, Fast review or Critical review."""

import pytest
from test_graph_generation import Fake, finish, service

from trajectory_remix.graph import phase_barrier
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.sampling import SamplerService


def operations(fake):
    return [c.payload["operation"] for c in fake.calls]


def logs(gen, job):
    return " ".join(item["text"] for item in gen.jobs.logs(job)["items"])


async def to_barrier(tmp_path, count=4, barriers=None):
    # service() caps active trajectories at 2; parked ones must not count toward it.
    gen, fake = service(tmp_path, method="draft_assembly")
    barriers = barriers or {"fast": True}
    job = gen.start([{"seed": 50 + i} for i in range(count)], {}, barriers)
    await finish(gen, job)
    return gen, fake, job


@pytest.mark.asyncio
async def test_all_trajectories_assemble_before_any_review_then_the_job_pauses(tmp_path):
    gen, fake, job = await to_barrier(tmp_path)
    assert operations(fake).count("draft") == 4 and operations(fake).count("assembly") == 4
    assert "review" not in operations(fake)
    assert gen.jobs.inspect(job)["state"] == "paused"
    assert phase_barrier.phase(gen.store, job) == 2  # The fast gate is open.
    assert "Phase barrier before fast: 4 trajectories are waiting" in logs(gen, job)

    active_at_review = []

    async def measure(ticket):
        active_at_review.append(
            list(phase_barrier.modes(gen.store, job).values()).count("attached")
        )
        return await fake(ticket)

    gen.transport = measure
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert [a["outcome"] for a in gen.jobs.attempts(job)] == ["pass"] * 4
    assert operations(fake)[8:].count("review") == 8  # Fast and Critical for each.
    assert max(active_at_review) == gen.jobs.active_limit


@pytest.mark.asyncio
async def test_the_barrier_survives_a_server_restart(tmp_path):
    gen, fake, job = await to_barrier(tmp_path, count=3)
    jobs = JobService(gen.store, active_limit=2)  # A new process recovers the store.
    restarted = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), Fake())
    assert phase_barrier.phase(restarted.store, job) == 2
    restarted.jobs.control(job, "resume")
    await finish(restarted, job)
    assert [a["outcome"] for a in restarted.jobs.attempts(job)] == ["pass"] * 3


@pytest.mark.asyncio
async def test_without_the_barrier_reviews_follow_assembly_directly(tmp_path):
    gen, fake = service(tmp_path, method="draft_assembly")
    job = gen.start([{"seed": 60}, {"seed": 61}], {})
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert "Phase barrier" not in logs(gen, job)


def phase_client(gen):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from trajectory_remix.graph.phase_api import add_phase_routes

    class Worker:
        kicked = []

        def kick(self, job):
            self.kicked.append(job)

    router, worker = APIRouter(), Worker()
    add_phase_routes(router, gen.jobs, worker)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), worker


def test_phase_routes_report_and_start_reviews_early(tmp_path):
    gen, _ = service(tmp_path, method="draft_assembly")
    job = gen.start([{"seed": 70}], {}, {"assembly": True})
    client, worker = phase_client(gen)
    assert client.get(f"/jobs/{job}/phase").json() == {
        "enabled": True,
        "phase": "generation",
        "barriers": {"assembly": True, "fast": False, "critical": False},
        "waiting": ["assembly"],
    }
    reply = client.post(f"/jobs/{job}/phase/review").json()
    assert reply["phase"] == "review" and reply["waiting"] == []
    assert "Barriers skipped" in logs(gen, job)


def test_start_reviews_needs_a_barrier(tmp_path):
    gen, _ = service(tmp_path, method="draft_assembly")
    job = gen.start([{"seed": 71}], {})
    client, _ = phase_client(gen)
    assert client.get(f"/jobs/{job}/phase").json()["enabled"] is False
    assert client.post(f"/jobs/{job}/phase/review").status_code == 409


@pytest.mark.asyncio
async def test_starting_reviews_early_lets_the_run_finish_without_pausing(tmp_path):
    gen, fake = service(tmp_path, method="draft_assembly")
    job = gen.start([{"seed": 72}, {"seed": 73}], {}, {"fast": True, "critical": True})
    phase_barrier.advance(gen.store, job)
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert "Phase barrier" not in logs(gen, job)


@pytest.mark.asyncio
async def test_a_barrier_before_assembly_stops_after_every_draft(tmp_path):
    gen, fake, job = await to_barrier(tmp_path, count=3, barriers={"assembly": True})
    assert operations(fake) == ["draft"] * 3
    assert gen.jobs.inspect(job)["state"] == "paused"
    assert "Phase barrier before assembly: 3 trajectories are waiting" in logs(gen, job)

    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert operations(fake).count("assembly") == 3


@pytest.mark.asyncio
async def test_each_enabled_barrier_pauses_in_turn(tmp_path):
    barriers = {"assembly": True, "fast": True, "critical": True}
    gen, fake, job = await to_barrier(tmp_path, count=4, barriers=barriers)
    assert set(operations(fake)) == {"draft"}
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert set(operations(fake)) == {"draft", "assembly"}
    assert "Phase barrier before fast" in logs(gen, job)
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    reviews = [
        c.payload["checkpoint"]["tier"] for c in fake.calls if c.payload["operation"] == "review"
    ]
    assert reviews == ["fast"] * 4
    assert gen.jobs.inspect(job)["state"] == "paused"
    assert "Phase barrier before critical" in logs(gen, job)
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"


@pytest.mark.asyncio
async def test_held_drafts_free_slots_for_pending_attempts_at_a_barrier(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    job = gen.start([{"seed": i} for i in range(50, 55)], {}, {"assembly": True})
    attempts = [a["trajectory_id"] for a in gen.jobs.attempts(job)]
    fail = set(attempts[1:3])

    async def fixture(ticket):
        if ticket.trajectory_id in fail:
            return {"context_limit": True, "model": "fixture"}
        return await fake(ticket)

    gen.transport = fixture
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "paused"
    assert phase_barrier.modes(gen.store, job) == {
        ident: "held" if ident in fail else "parked" for ident in attempts
    }
    assert "Phase barrier before assembly: 3 trajectories are waiting (2 held" in logs(gen, job)

    fail.clear()
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert [a["outcome"] for a in gen.jobs.attempts(job)] == ["pass"] * 5
    assert all("needs_attention" not in gen.store.read(i).source.metadata for i in attempts)


@pytest.mark.asyncio
async def test_only_a_critical_barrier_lets_fast_review_run_first(tmp_path):
    gen, fake, job = await to_barrier(tmp_path, count=2, barriers={"critical": True})
    tiers = [
        c.payload["checkpoint"]["tier"] for c in fake.calls if c.payload["operation"] == "review"
    ]
    assert tiers == ["fast", "fast"]
    assert gen.jobs.inspect(job)["state"] == "paused"


def test_barriers_are_validated_and_frozen_with_the_run(tmp_path):
    gen, _ = service(tmp_path, method="draft_assembly")
    with pytest.raises(ValueError):
        gen.start([{"seed": 80}], {}, {"review": True})
    with pytest.raises(ValueError):
        gen.start([{"seed": 80}], {}, {"fast": "yes"})
    job = gen.start([{"seed": 80}], {}, {"fast": True})
    frozen = gen.jobs.inspect(job)["config"]["config"]["barriers"]
    assert frozen == {"assembly": False, "fast": True, "critical": False}


def test_the_retired_settings_flag_is_ignored(tmp_path):
    from trajectory_remix.graph.models import GenerationOptions

    assert GenerationOptions.model_validate({"phase_barrier": True}).fast is True
