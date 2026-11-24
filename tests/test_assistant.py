"""Send to agent: a native assistant session fixes a trajectory's open comments, then replies."""

import json
from uuid import uuid4

import pytest
from test_graph_generation import Fake, finish, plan, settings, tool_message
from test_graph_studio import client, create  # noqa: F401  (pytest fixture)

from trajectory_remix.graph import assistant, assistant_memory, endpoint_prompts, prompt_defaults
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.comments import CommentService, NewComment, NewReply
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.provider import request_body
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
    return record, comment(gen, record, *bodies)


def comment(gen, record, *bodies):
    return [
        gen.comments.add(
            record.source.id, NewComment(target={"kind": "event", "id": "x"}, body=b), "user"
        )["id"]
        for b in bodies
    ]


def edit(content="after"):
    return ("update_event", {"event_id": "x", "content": content})


def reply(ident, done, note):
    return ("reply", {"comment_id": ident, "done": done, "note": note})


def turn(*calls):
    return lambda t: tool_message(*calls)


def results(payload):
    return [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"]


async def run(gen, record):
    job = gen.start_assistant(record.source.id, record.revision, gen.comments)
    await finish(gen, job)
    return job


@pytest.mark.asyncio
async def test_assistant_edits_then_replies_with_native_tools(tmp_path):
    gen, fake = service(tmp_path)
    record, (fixed, unclear) = flagged(gen, "Content is wrong.", "Is this | the right\nwindow?")
    gen.comments.reply(unclear, NewReply(body="I think reason is wrong."), "user")
    fake.responses = [
        turn(edit(), reply(fixed, True, "Rewrote x."), reply(unclear, False, "Which window?"))
    ]
    job = await run(gen, record)
    source = gen.store.read(record.source.id).source
    assert source.events["x"].content == "after"
    assert source.metadata["stage"] == 1, "the assistant never changes the stage"
    (first,) = fake.calls
    assert first.endpoint == "a" and first.payload["operation"] == "assist"
    names = [tool["function"]["name"] for tool in first.payload["tools"]]
    assert "reply" in names and "update_event" in names and "signoff" not in names
    task = first.payload["messages"][1]["content"]
    assert task.startswith(prompt_defaults.assistant() + "\n\n| ID | Target |")
    assert f"| `{fixed}` | event `x` | Content is wrong. | - |" in task
    assert "Is this \\| the right<br>window?" in task and "user: I think reason is wrong." in task
    assert f"Trajectory Id: `{record.source.id}`" in task
    assert task.endswith(f"Trajectory Id: `{record.source.id}`")
    states = {c["id"]: c for c in gen.comments.for_trajectory(record.source.id)}
    assert states[fixed]["status"] == "addressed", "only the user resolves"
    assert states[fixed]["replies"][-1]["body"] == "Rewrote x."
    assert states[unclear]["status"] == "open"
    assert states[unclear]["replies"][-1]["author"] == "agent"
    assert states[unclear]["replies"][-1]["body"] == "Which window?"
    assert gen.jobs.inspect(job)["state"] == "completed"


@pytest.mark.asyncio
async def test_the_session_continues_until_every_comment_has_one_reply(tmp_path):
    gen, fake = service(tmp_path)
    record, (one, two) = flagged(gen, "First.", "Second.")
    fake.responses = [
        turn(reply(one, True, "Fixed."), reply(one, True, "Again.")),
        {"role": "assistant", "content": "All done."},
        turn(("signoff", {}), reply(two, False, "Why?")),
    ]
    await run(gen, record)
    assert len(fake.calls) == 3
    first, duplicate = results(fake.calls[1].payload)
    assert first == {**first, "accepted": True, "remaining": [two]}
    assert duplicate["accepted"] is False and "already replied" in duplicate["error"]
    nudge = fake.calls[2].payload["messages"][-1]
    assert nudge["role"] == "user"
    assert f"You have not replied to comment `{two}`" in nudge["content"]
    assert one not in nudge["content"]
    kept = assistant_memory.load(gen.store, record.source.id)
    signoff, answered = (json.loads(m["content"]) for m in kept[-2:])
    assert signoff["accepted"] is False and "unavailable" in signoff["error"]
    assert answered["accepted"] is True and answered["remaining"] == []
    states = {c["id"]: c["status"] for c in gen.comments.for_trajectory(record.source.id)}
    assert states == {one: "addressed", two: "open"}


@pytest.mark.asyncio
async def test_comments_are_frozen_at_send(tmp_path):
    gen, fake = service(tmp_path)
    record, (first,) = flagged(gen, "Before sending.")
    job = gen.start_assistant(record.source.id, record.revision, gen.comments)
    gen.comments.add(
        record.source.id, NewComment(target={"kind": "trajectory"}, body="Later."), "user"
    )
    fake.responses = [turn(reply(first, True, "Done."))]
    await finish(gen, job)
    assert "Later." not in fake.calls[0].payload["messages"][1]["content"]
    later = [c for c in gen.comments.for_trajectory(record.source.id) if c["body"] == "Later."]
    assert later[0]["status"] == "open"


@pytest.mark.asyncio
async def test_agent_and_authoring_prompts_are_frozen_for_each_request(tmp_path):
    gen, fake = service(tmp_path)
    current = gen.store.settings()
    data = current.settings.model_dump(mode="json")
    prompts = dict(actor="Use the project persona.", authoring="Tool guide.")
    gen.store.update_settings(data | {"prompts": data["prompts"] | prompts}, current.revision)
    record, (only,) = flagged(gen, "Fix x.")
    job = gen.start_assistant(record.source.id, record.revision, gen.comments)
    current = gen.store.settings()
    later = data["prompts"] | prompts | {"actor": "A later persona."}
    gen.store.update_settings(data | {"prompts": later}, current.revision)
    fake.responses = [turn(edit()), turn(reply(only, True, "Fixed."))]
    await finish(gen, job)

    first, second = (ticket.payload for ticket in fake.calls)
    assert first["messages"][0]["content"] == "Use the project persona.\n\nTool guide."
    assert first["messages"] == second["messages"][: len(first["messages"])]
    assert first["prompt_version"] == second["prompt_version"] == assistant.VERSION
    body, _ = request_body(first, record.source.id)
    assert body["messages"] == first["messages"] and body["tools"] == first["tools"]


@pytest.mark.asyncio
async def test_a_later_send_continues_the_conversation(tmp_path):
    gen, fake = service(tmp_path)
    record, (old,) = flagged(gen, "First pass.")
    fake.responses = [turn(edit(), reply(old, True, "Rewrote x."))]
    await run(gen, record)
    kept = assistant_memory.load(gen.store, record.source.id)
    assert kept[-1]["role"] == "tool" and len(kept) == 5  # system, task, call, 2 results

    (new,) = comment(gen, gen.store.read(record.source.id), "Now shorten it.")
    record = gen.store.read(record.source.id)
    fake.responses = [turn(edit("short"), reply(new, True, "Shortened."))]
    await run(gen, record)
    messages = fake.calls[-1].payload["messages"]
    assert messages[: len(kept)] == kept
    follow_up = messages[len(kept)]
    assert follow_up["role"] == "user"
    assert follow_up["content"].startswith(endpoint_prompts.FOLLOW_UP)
    assert "Now shorten it." in follow_up["content"] and "First pass." not in follow_up["content"]
    assert gen.store.read(record.source.id).source.events["x"].content == "short"


@pytest.mark.asyncio
async def test_an_oversized_kept_conversation_starts_afresh(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Fix x.")
    huge = [dict(role="system", content="old"), dict(role="user", content="x" * 300_000)]
    assistant_memory.save(gen.store, record.source.id, huge)
    fake.responses = [turn(reply(only, True, "Done."))]
    await run(gen, record)
    messages = fake.calls[0].payload["messages"]
    assert len(messages) == 2 and messages[0]["content"] != "old"


@pytest.mark.asyncio
async def test_exchange_limit_ends_the_session_and_keeps_comments_open(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Never answered.")
    current = gen.store.settings()
    data = current.settings.model_dump(mode="json")
    data["generation"]["max_authoring_exchanges"] = 2
    gen.store.update_settings(data, current.revision)
    fake.responses = [turn(edit()), turn(("view_graph", {}))]
    job = await run(gen, record)
    assert len(fake.calls) == 2 and gen.jobs.inspect(job)["state"] == "completed"
    assert gen.store.read(record.source.id).source.events["x"].content == "after"
    comment = gen.comments.for_trajectory(record.source.id)[0]
    assert comment["status"] == "open"
    assert comment["replies"][-1]["author"] == "agent"
    assert comment["replies"][-1]["body"] == "Failed to execute task. Exchange limit reached."
    assert assistant_memory.load(gen.store, record.source.id) is None


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
    fake.responses = [turn(("view_graph", {}))] * 400
    current = gen.store.settings()
    tight = current.settings.model_dump(mode="json")
    tight["generation"]["assistant_context_bytes"] = 256
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
