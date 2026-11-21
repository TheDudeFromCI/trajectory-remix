"""Review roles are independently optional for generated and selected trajectories."""

import pytest
from test_graph_compiler import event, source
from test_graph_generation import finish, service

from trajectory_remix.graph.models import ProjectSettings


def review_roles(gen, roles):
    snapshot = gen.store.settings()
    settings = snapshot.settings
    settings.endpoint_roles = {"draft": "g", **roles}
    gen.store.update_settings(settings, snapshot.revision)


@pytest.mark.parametrize(
    "roles, expected, stage",
    [
        ({"fast": "none", "critical": "c"}, ["critical"], 2),
        ({"fast": "f", "critical": None}, ["fast"], 1),
        ({}, [], 0),
    ],
)
@pytest.mark.asyncio
async def test_generate_skips_only_unassigned_review_roles(tmp_path, roles, expected, stage):
    gen, fake = service(tmp_path)
    review_roles(gen, roles)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    attempt = gen.jobs.attempts(job)[0]
    assert attempt["outcome"] == "pass"
    saved = gen.store.read(attempt["trajectory_id"]).source
    assert saved.metadata["stage"] == stage
    reviews = [t for t in fake.calls if t.payload["operation"] == "review"]
    assert [t.payload["tier"] for t in reviews] == expected
    if expected == ["critical"]:
        assert {"core.causal_evidence", "critical.training_value"} <= (
            reviews[0].payload["checkpoint"]["authoring_session"]["checks"].keys()
        )


@pytest.mark.asyncio
async def test_skipped_fast_barrier_does_not_prevent_critical_barrier(tmp_path):
    gen, fake = service(tmp_path, fast=False, critical=True)
    job = gen.start([{"seed": 137}], {}, {"fast": True, "critical": True})
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "paused"
    assert [t.payload["operation"] for t in fake.calls] == ["draft", "assembly"]
    logs = [line["text"] for line in gen.jobs.logs(job)["items"]]
    assert any("Phase barrier before critical:" in line for line in logs)
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.jobs.attempts(job)[0]["outcome"] == "pass"
    assert [t.payload["tier"] for t in fake.calls if t.payload["operation"] == "review"] == [
        "critical"
    ]


@pytest.mark.asyncio
async def test_manual_pending_review_can_use_critical_directly(tmp_path):
    gen, fake = service(tmp_path)
    review_roles(gen, {"critical": "c"})
    record = gen.store.create(source({"note": event("agent_text", "notes")}, metadata={"stage": 0}))
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    assert [t.payload["tier"] for t in fake.calls] == ["critical"]
    assert gen.store.read(record.source.id).source.metadata["stage"] == 2


def test_none_remains_available_as_an_explicit_endpoint_id():
    endpoints = {"none": {"url": "http://127.0.0.1:1/v1", "model": "fixture"}}
    assert ProjectSettings.model_validate(
        {"endpoints": endpoints, "endpoint_roles": {"fast": "none"}}
    ).endpoint_roles == {"fast": "none"}
