"""Batch generation uses the scenario runtime and resumes unfinished batches."""

import asyncio

import httpx
import pytest
from test_scenarios import ScenarioClient, app_for

from trajectory_remix.server import create_app


async def complete(runner):
    # The per-sample allowance must also cover multi-sample batch tests.
    await asyncio.wait_for(runner.job, 15 * runner.status["batch"]["target"])


async def test_batch_creates_searchable_candidates_and_keeps_model_metrics_local(
    compiler, tmp_path
):
    fake = ScenarioClient()
    app = app_for(tmp_path, compiler, fake, legacy_reviews=False)
    runner = app.state.scenario_runner
    runner.start("user_problem", batch_size=3)
    await complete(runner)
    batch = runner.status["batch"]
    assert batch["state"] == "complete" and batch["completed"] == 3
    assert batch["attempts"] == 3
    assert runner.status["accepted"] == 3
    assert runner.status["attempts"] == 3
    samples = list(app.state.dataset["samples"])
    assert len(samples) == 3
    assert len({s["id"] for s in samples}) == 3
    assert all(s["metadata"]["validation_level"] == 0 for s in samples)
    assert all(s["metadata"]["generation_batch"] == batch["id"] for s in samples)
    assert app.state.dataset.store.listing(query=batch["id"])[0] == 3
    assert app.state.dataset.store.listing(
        query=f"@state:pending @batch:{batch['id']}", limit=5
    )[0] == 3
    assert runner.status["model_requests"] < len(fake.messages)


async def test_failed_candidates_do_not_fulfill_batch_target_and_attempts_are_bounded(
    compiler, tmp_path
):
    class InvalidContent(ScenarioClient):
        async def complete(self, endpoint, messages, tools=None):
            if messages[0]['content'].startswith('Invent'):
                return {'content': 'malformed generated content'}
            return await super().complete(endpoint, messages, tools)
    app = app_for(tmp_path, compiler, InvalidContent())
    app.state.dataset["config"].setdefault("single_generation", {})["max_attempts"] = 2
    runner = app.state.scenario_runner
    runner.registry.scenarios["user_problem"].flow.steps["validate"].max_repairs = 0
    runner.start("user_problem", batch_size=2)
    await complete(runner)
    assert runner.status["batch"]["state"] == "exhausted"
    assert runner.status["batch"]["completed"] == 0
    assert runner.status["batch"]["attempts"] == 4
    assert not runner.status["running"]
    assert len(app.state.dataset["samples"]) == 4
    assert all(
        s["metadata"]["validation_level"] == -1 for s in app.state.dataset["samples"]
    )


async def test_api_outage_pauses_batch_and_resume_finishes_remaining_samples(
    compiler, tmp_path
):
    class Outage(ScenarioClient):
        broken = True

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Invent") and self.broken:
                raise httpx.ConnectError("offline")
            return await super().complete(endpoint, messages, tools)

    fake = Outage()
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem", batch_size=2)
    await complete(runner)
    assert runner.status["phase"] == "api_paused"
    assert runner.status["batch"]["state"] == "paused"
    assert runner.status["batch"]["attempts"] == 1
    ident = runner.status["draft_id"]
    fake.broken = False
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(ident)
    await complete(resumed)
    assert resumed.status["batch"]["state"] == "complete"
    assert resumed.status["batch"]["completed"] == 2
    assert resumed.status["attempts"] == 2


async def test_cancel_batch_preserves_finished_samples_and_resumes(compiler, tmp_path):
    class StopSecond(ScenarioClient):
        ideas = 0

        async def complete(self, endpoint, messages, tools=None):
            if messages[0]["content"].startswith("Invent"):
                self.ideas += 1
                if self.ideas == 2:
                    self.entered.set()
                    await self.release.wait()
            return await super().complete(endpoint, messages, tools)

    fake = StopSecond()
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.start("user_problem", batch_size=3)
    await asyncio.wait_for(fake.entered.wait(), 10)
    assert runner.status["batch"]["completed"] == 1
    first_id = app.state.dataset["samples"][0]["id"]
    ident = runner.status["draft_id"]
    await runner.stop()
    assert runner.status["batch"]["state"] == "cancelled"
    fake.release.set()
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(ident)
    await complete(resumed)
    assert resumed.status["batch"]["completed"] == 3
    assert resumed.status["batch"]["attempts"] == 3
    assert restored.state.dataset["samples"][0]["id"] == first_id
    assert len(restored.state.dataset["samples"]) == 3


@pytest.mark.parametrize("size", [0, -1, 1001, True, 1.5, "3"])
async def test_batch_api_rejects_invalid_counts_without_starting(
    compiler, tmp_path, size
):
    app = app_for(tmp_path, compiler, ScenarioClient())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        response = await client.post(
            "/api/scenarios/run", json={"scenario": "user_problem", "batch_size": size}
        )
    assert response.status_code == 422
    assert not app.state.scenario_runner.status["running"]
    assert len(app.state.dataset["samples"]) == 0


async def test_batch_keeps_shared_request_limit_between_samples(compiler, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    manual_task = None

    class WaitAtBoundary(ScenarioClient):
        ideas = 0

        async def complete(self, endpoint, messages, tools=None):
            nonlocal manual_task
            if messages[0]["content"].startswith("Invent"):
                self.ideas += 1
            if (
                messages[0]["content"].startswith("Decide")
                and self.ideas == 1
            ):

                async def hold_slot():
                    async with runner.model_slot():
                        entered.set()
                        await release.wait()

                manual_task = asyncio.create_task(hold_slot())
            return await super().complete(endpoint, messages, tools)

    fake = WaitAtBoundary()
    runner = app_for(tmp_path, compiler, fake).state.scenario_runner
    runner.start("user_problem", batch_size=2)
    await asyncio.wait_for(entered.wait(), 10)
    await asyncio.sleep(0.2)
    assert runner.status["batch"]["attempts"] == 2
    assert fake.ideas == 1
    release.set()
    await manual_task
    await complete(runner)
    assert runner.status["batch"]["completed"] == 2


async def test_pause_at_sample_boundary_leaves_a_resumable_planning_draft(
    compiler, tmp_path
):
    fake = ScenarioClient()
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    original = runner.run_attempt
    paused = asyncio.Event()

    async def pause_after_first():
        await original()
        if not paused.is_set():
            runner.pause()
            paused.set()

    runner.run_attempt = pause_after_first
    runner.start("user_problem", batch_size=2)
    await asyncio.wait_for(paused.wait(), 10)
    assert runner.status["paused"] and runner.status["running"]
    assert runner.status["batch"]["completed"] == 1
    ident = runner.status["draft_id"]
    assert runner.can_resume(ident)
    await runner.stop()
    restored = create_app(tmp_path / "project.sqlite", client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(ident)
    await complete(resumed)
    assert resumed.status["batch"]["completed"] == 2
    assert resumed.status["batch"]["attempts"] == 2
