"""Send to agent: an assistant session fixes a trajectory's open comments, then answers them."""

import json
from uuid import uuid4

import pytest
from test_graph_generation import Fake, finish, plan, settings
from test_graph_studio import client, create  # noqa: F401  (pytest fixture)

from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.comments import CommentService, NewComment, NewReply
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore


def service(tmp_path, assistant=True):
    config = settings()
    if assistant:
        config["endpoints"]["a"] = dict(url="http://127.0.0.1:1/v1", model="a", retries=0)
        config["endpoint_roles"]["assistant"] = "a"
    store = GraphStore.initialize(tmp_path / "p.sqlite", config)
    jobs = JobService(store, active_limit=2)
    fake = Fake()
    gen = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), fake)
    gen.comments = CommentService(store)
    return gen, fake


def flagged(gen, *bodies):
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan()["windows"],
            events={"x": dict(kind="agent_text", window="reason", content="before")},
            metadata={"stage": 1},
        )
    )
    ids = [
        gen.comments.add(
            record.source.id, NewComment(target={"kind": "event", "id": "x"}, body=b), "user"
        )["id"]
        for b in bodies
    ]
    return record, ids


def patch(t):
    return {
        "action": "patch",
        "event_id": "x",
        "expected_source_hash": t.payload["source_hash"],
        "patch": {"content": "after"},
    }


def finish_with(*answers):
    return {
        "action": "finish",
        "comments": [dict(id=i, done=r, note=n) for i, r, n in answers],
        "summary": "done",
    }


async def run(gen, record):
    job = gen.start_assistant(record.source.id, record.revision, gen.comments)
    await finish(gen, job)
    return job


@pytest.mark.asyncio
async def test_assistant_patches_then_addresses_and_replies(tmp_path):
    gen, fake = service(tmp_path)
    record, (fixed, unclear) = flagged(gen, "Content is wrong.", "Is this the right window?")
    gen.comments.reply(unclear, NewReply(body="I think reason is wrong."), "user")
    fake.responses = [
        patch,
        lambda t: finish_with((fixed, True, "Rewrote x."), (unclear, False, "Which window?")),
    ]
    job = await run(gen, record)
    source = gen.store.read(record.source.id).source
    assert source.events["x"].content == "after"
    assert source.metadata["stage"] == 1, "the assistant never changes the stage"
    first = fake.calls[0]
    assert first.endpoint == "a" and first.payload["operation"] == "assist"
    brief = first.payload["messages"][1]["content"]
    assert "Content is wrong." in brief and "I think reason is wrong." in brief
    assert "metadata" not in json.loads(brief)["source"]
    states = {c["id"]: c for c in gen.comments.for_trajectory(record.source.id)}
    assert states[fixed]["status"] == "addressed", "only the user resolves"
    assert states[fixed]["replies"][-1]["body"] == "Rewrote x."
    assert states[unclear]["status"] == "open"
    assert states[unclear]["replies"][-1] == {**states[unclear]["replies"][-1], "author": "agent"}
    assert states[unclear]["replies"][-1]["body"] == "Which window?"
    assert gen.jobs.inspect(job)["state"] == "completed"


@pytest.mark.asyncio
async def test_finish_must_answer_every_comment(tmp_path):
    gen, fake = service(tmp_path)
    record, (one, two) = flagged(gen, "First.", "Second.")
    fake.responses = [
        lambda t: finish_with((one, True, "Fixed.")),
        lambda t: finish_with((one, True, "Fixed."), (two, True, "Also fixed.")),
    ]
    await run(gen, record)
    correction = fake.calls[1].payload["messages"][-1]["content"]
    assert "output contract" in correction
    assert all(c["status"] == "addressed" for c in gen.comments.for_trajectory(record.source.id))


@pytest.mark.asyncio
async def test_comments_are_frozen_at_send(tmp_path):
    gen, fake = service(tmp_path)
    record, (first,) = flagged(gen, "Before sending.")
    job = gen.start_assistant(record.source.id, record.revision, gen.comments)
    gen.comments.add(
        record.source.id, NewComment(target={"kind": "trajectory"}, body="Later."), "user"
    )
    fake.responses = [lambda t: finish_with((first, True, "Done."))]
    await finish(gen, job)
    assert "Later." not in fake.calls[0].payload["messages"][1]["content"]
    later = [c for c in gen.comments.for_trajectory(record.source.id) if c["body"] == "Later."]
    assert later[0]["status"] == "open"


def test_send_requires_the_role_open_comments_and_current_revision(tmp_path):
    gen, _ = service(tmp_path, assistant=False)
    record, _ = flagged(gen, "x")
    with pytest.raises(ValueError, match="Assistant endpoint role"):
        gen.start_assistant(record.source.id, record.revision, gen.comments)
    gen, _ = service(tmp_path / "with-role")
    record = gen.store.create(
        dict(format="kidra-trajectory-graph/1", id=str(uuid4()), windows={}, events={})
    )
    with pytest.raises(ValueError, match="no open comments"):
        gen.start_assistant(record.source.id, record.revision, gen.comments)
    record, _ = flagged(gen, "x")
    with pytest.raises(ValueError, match="Revision conflict"):
        gen.start_assistant(record.source.id, record.revision + 1, gen.comments)


@pytest.mark.asyncio
async def test_context_limit_ends_the_session_and_keeps_comments_open(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Too much.")
    fake.responses = [{"action": "view"}] * 400
    current = gen.store.settings()
    tight = current.settings.model_dump(mode="json")
    tight["generation"]["repair_context_bytes"] = 256
    gen.store.update_settings(tight, current.revision)
    job = await run(gen, record)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert gen.comments.for_trajectory(record.source.id)[0]["status"] == "open"
    assert not fake.calls, "an over-budget first request is never sent"


def test_rest_route_explains_a_missing_role(client):  # noqa: F811
    created = create(client)
    ident = created["source"]["id"]
    body = {"target": {"kind": "trajectory"}, "body": "Fix this."}
    client.post(f"/api/v1/trajectories/{ident}/comments", json=body)
    r = client.post(f"/api/v1/trajectories/{ident}/assistant", json={"expected_revision": 1})
    assert r.status_code == 422 and "Assistant endpoint role" in r.text
    roles = client.get("/api/v1/schema/settings").json()["properties"]["endpoint_roles"]
    assert "assistant" in str(roles)
