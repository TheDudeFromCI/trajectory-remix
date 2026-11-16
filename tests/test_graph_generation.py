"""Tiny deterministic handoff-06 checks; no real provider, compiler or benchmark."""

import asyncio
import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph import reviews
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.flow import causal_view, prepare_plan
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import Fenced, JobService, OwnershipConflict
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore, RevisionConflict
from trajectory_remix.graph.studio_api import create_studio_app


def settings(**options):
    return dict(
        endpoints={
            k: dict(url="http://127.0.0.1:1/v1", model=k, retries=0) for k in ("g", "f", "c")
        },
        endpoint_roles={"generator": "g", "fast": "f", "critical": "c"},
        tools={
            "chat": dict(
                name="Chat",
                input_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
            ),
            "read": dict(name="Read", input_schema={"type": "object"}),
        },
        generation=options,
    )


def plan():
    return {
        "windows": {
            "room": {"purpose": "chatroom", "agent_writable": False},
            "reason": {"purpose": "reasoning", "agent_writable": True},
            "call": {"purpose": "tool", "agent_writable": True},
            "doc": {"purpose": "document", "agent_writable": False},
        },
        "tasks": [
            dict(
                id="user",
                envelope=dict(kind="chat_message", window="room", chat={"speaker": "Alice"}),
            ),
            dict(
                id="other",
                envelope=dict(
                    kind="chat_message", window="room", chat={"speaker": "Bob"}, after=["user"]
                ),
            ),
            dict(id="wake", envelope=dict(kind="external_text", window="doc", after=["other"])),
            dict(id="think", envelope=dict(kind="agent_text", window="reason", after=["wake"])),
            dict(
                id="call",
                arguments="json",
                envelope=dict(
                    kind="tool_call",
                    tool="chat",
                    call_id="chat-1",
                    window="call",
                    after=["think"],
                    close_window=True,
                ),
            ),
        ],
    }


def report(payload, status="pass", source_ids=()):
    schema = payload["schema"]
    if payload["operation"] == "repair":
        schema = schema["oneOf"][2]["properties"]["review"]
    return dict(
        items={
            key: dict(
                status=status, evidence="Observed causal evidence.", event_ids=list(source_ids)
            )
            for key in schema["properties"]["items"]["properties"]
        },
        summary="offline fixture",
    )


class Fake:
    def __init__(self):
        self.calls = []
        self.responses = []

    async def __call__(self, ticket):
        self.calls.append(copy.deepcopy(ticket))
        p = ticket.payload
        if self.responses:
            value = self.responses.pop(0)
            if callable(value):
                value = value(ticket)
            return {"output": value, "model": "actual-fixture"}
        if p["operation"] == "flow_select":
            source = json.loads(p["messages"][1]["content"])["source"]
            if "reply" in source["events"]:
                body = dict(
                    finished=True, reason="Delivered the fixture reply", windows={}, task=None
                )
            else:
                steps = [
                    (
                        "opening",
                        dict(kind="chat_message", window="chatroom", chat={"speaker": "user"}),
                    ),
                    ("decision", dict(kind="agent_text", window="reasoning", after=["opening"])),
                    (
                        "reply",
                        dict(
                            kind="tool_call",
                            window="reply",
                            after=["decision"],
                            tool="chat",
                            call_id="reply",
                            close_window=True,
                        ),
                    ),
                ]
                ident, envelope = next(
                    (key, event) for key, event in steps if key not in source["events"]
                )
                body = dict(
                    finished=False,
                    reason="Continue the fixture episode",
                    windows={}
                    if source["windows"]
                    else {
                        "chatroom": dict(purpose="chatroom", agent_writable=False),
                        "reasoning": dict(purpose="reasoning", agent_writable=True),
                        "reply": dict(purpose="tool", agent_writable=True),
                    },
                    task=dict(
                        id=ident,
                        envelope=envelope,
                        instruction="Author the fixture event",
                        arguments="json" if ident == "reply" else "text",
                    ),
                )
        elif p["operation"] == "event":
            body = (
                {"arguments": {"content": "Supported reply"}}
                if "arguments" in p["schema"]["properties"]
                else {"content": "Visible observation " + p["task"]["id"]}
            )
        elif p["operation"] == "repair":
            body = {"action": "verdict", "review": report(p)}
        else:
            body = report(p)
        return {"output": body, "model": "actual-fixture"}


def service(tmp_path, fake=None, **opts):
    store = GraphStore.initialize(tmp_path / "p.sqlite", settings(**opts))
    jobs = JobService(store, active_limit=2)
    sampler, bulk = SamplerService(jobs), BulkService(jobs)
    fake = fake or Fake()
    return GenerationService(jobs, sampler, bulk, fake), fake


async def finish(gen, job):
    # This deadline catches hangs; it is not a performance acceptance threshold.
    # Adaptive episodes make many durable transitions and deliberately poll
    # between them. Shared CI runners can exceed five seconds (notably 3.13).
    await asyncio.wait_for(gen.run(gen.jobs, job), 30)
    assert gen.jobs.inspect(job)["state"] in {"completed", "paused", "cancelled"}


@pytest.mark.asyncio
async def test_event_flow_reviews_snapshots_echo_and_ownership(tmp_path, monkeypatch):
    # A compile-free guard covers generation, review and repairs transitively.
    from trajectory_remix.graph.compiler import GraphCompiler

    monkeypatch.setattr(
        GraphCompiler, "__init__", lambda *a, **k: pytest.fail("generation compiled")
    )
    gen, fake = service(tmp_path)
    job = gen.start([{"seed": 137}, {"seed": 138}], {"plan": plan()})
    frozen = gen.jobs.inspect(job)["config"]
    current = gen.store.settings()
    updated = current.settings.model_dump(mode="json")
    updated["endpoints"]["g"]["model"] = "edited-later"
    gen.store.update_settings(updated, current.revision)
    await finish(gen, job)
    attempts = gen.jobs.attempts(job)
    assert len(attempts) == 2 and all(
        a["state"] == "detached" and a["outcome"] == "pass" for a in attempts
    )
    for a in attempts:
        record = gen.store.read(a["trajectory_id"])
        source = record.source
        assert source.metadata["stage"] == 2
        assert source.metadata["targets"] == a["proposal"]["proposal"]["metrics"]
        assert source.events["call:echo"].chat.speaker == "agent"
        assert source.events["call:echo"].after == ["call", "other", "user"]
        assert (
            len(
                [
                    t
                    for t in fake.calls
                    if t.trajectory_id == source.id and t.payload["operation"] == "event"
                ]
            )
            == 5
        )
        assert [e["tier"] for e in source.metadata["evaluations"]] == ["fast", "critical"]
        assert source.metadata["evaluations"][0]["model"] == "actual-fixture"
        assert reviews.stale(source, gen.settings_for(job))["stale"] is False
        with pytest.raises(RevisionConflict):
            gen.jobs.acquire(gen.jobs.create_job("manual"), source.id, record.revision - 1)
    assert gen.jobs.inspect(job)["config"] == frozen
    assert all(t.payload["endpoint_config"]["model"] != "edited-later" for t in fake.calls)
    actors = [
        t
        for t in fake.calls
        if t.payload["operation"] == "event"
        and t.payload["task"]["envelope"]["kind"] in ("agent_text", "tool_call")
    ]
    assert all(
        "learning_objective" not in t.payload["messages"][1]["content"]
        and "world" not in json.loads(t.payload["messages"][1]["content"])
        for t in actors
    )


def test_causal_frontiers_replacement_close_parallel(tmp_path):
    gen, _ = service(tmp_path)
    source = GraphSource.model_validate(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows={
                k: dict(purpose="document", agent_writable=False)
                for k in ["doc", "closed", "parallel"]
            },
            events={
                "old": dict(kind="external_text", window="doc", content="SECRET_OLD"),
                "new": dict(
                    kind="external_text",
                    window="doc",
                    content="current",
                    after=["old"],
                    replace_body=True,
                ),
                "closed": dict(
                    kind="external_text",
                    window="closed",
                    content="SECRET_CLOSED",
                    close_window=True,
                ),
                "parallel": dict(
                    kind="external_text", window="parallel", content="SECRET_PARALLEL"
                ),
            },
        )
    )
    text = json.dumps(causal_view(source, ["new", "closed"]))
    assert "current" in text and "SECRET" not in text
    with pytest.raises(ValueError):
        prepare_plan(
            gen.sampler.propose(seed=137)["proposal"],
            gen.store.settings().settings,
            {
                "plan": {
                    "windows": plan()["windows"],
                    "tasks": [
                        dict(
                            id="x",
                            world={"secret": "x"},
                            envelope=dict(kind="agent_text", window="reason"),
                        )
                    ],
                }
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_partial_calls_results_free_form_and_chat_opt_out(tmp_path, streamed):
    gen, fake = service(tmp_path, fast=False)
    p = plan()
    p["tasks"] = [
        p["tasks"][0],
        dict(
            id="prefix",
            envelope=dict(
                kind="tool_call",
                tool="chat",
                call_id="partial",
                window="call",
                after=["user"],
                insert_tool_end=False,
            ),
        ),
        dict(
            id="retrieval",
            envelope=dict(
                kind="external_text", window="doc", content_placeholder=False, after=["prefix"]
            ),
        ),
        dict(
            id="suffix",
            envelope=dict(
                kind="tool_call",
                tool="chat",
                call_id="partial",
                window="call",
                after=["retrieval"],
                insert_tool_begin=False,
                echo_chat=False,
            ),
        ),
        dict(
            id="result",
            envelope=dict(
                kind="tool_response",
                call_id="partial",
                window="doc",
                after=["suffix"],
                delivery={
                    "mode": "streamed" if streamed else "instant",
                    "chain_immediately": False,
                },
                replace_body=True,
            ),
        ),
    ]
    del p["tasks"][2]["envelope"]["content_placeholder"]
    fake.responses = [
        {"content": "question"},
        {"content": '{"content":"'},
        {"content": "retrieved fact"},
        {"content": 'message"}'},
        {"content": "arbitrary document: [not JSON, nested errors"},
    ]
    job = gen.start([{"seed": 137}], {"plan": p})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert len(source.events) == 5
    assert source.events["result"].content.startswith("arbitrary document")
    assert source.events["result"].delivery.mode == ("streamed" if streamed else "instant")
    context = json.loads(fake.calls[3].payload["messages"][1]["content"])["visible"]
    assert "retrieved fact" in json.dumps(context)
    assert source.metadata["stage"] == 0


@pytest.mark.asyncio
async def test_generation_format_failure_no_replacement(tmp_path):
    gen, fake = service(tmp_path, fast=False, repair=False, format_retries=1)
    fake.responses = [{"wrong": "x"}, {"wrong": "x"}]
    job = gen.start([{"seed": 137}], {"plan": plan()})
    await finish(gen, job)
    attempts = gen.jobs.attempts(job)
    source = gen.store.read(attempts[0]["trajectory_id"]).source
    assert len(fake.calls) == 2 and len(attempts) == 1 and attempts[0]["outcome"] == "fail"
    assert source.metadata["stage"] == -1 and source.events == {}


@pytest.mark.asyncio
async def test_malformed_review_pauses_without_verdict(tmp_path):
    gen, fake = service(tmp_path, format_retries=0)
    fake.responses = [
        *({"content": "x"} for _ in range(4)),
        {"arguments": {"content": "reply"}},
        {"items": {}},
    ]
    job = gen.start([{"seed": 137}], {"plan": plan()})
    await finish(gen, job)
    a = gen.jobs.attempts(job)[0]
    source = gen.store.read(a["trajectory_id"]).source
    assert source.metadata["stage"] == 0 and "evaluations" not in source.metadata
    assert a["state"] == "active"
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.store.read(a["trajectory_id"]).source.metadata["stage"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["negative_verdict", "context_limit", None])
async def test_continuing_repair_keeps_patches_and_one_verdict(tmp_path, failure):
    gen, fake = service(tmp_path, critical=False)
    # Start from a detached failed source for a compact manual session.
    source = dict(
        format="kidra-trajectory-graph/1",
        id=str(uuid4()),
        windows=plan()["windows"],
        events={"x": dict(kind="agent_text", window="reason", content="before")},
        metadata={"stage": -1},
    )
    record = gen.store.create(source)
    fake.responses = [
        lambda t: {
            "action": "patch",
            "event_id": "x",
            "expected_source_hash": t.payload["source_hash"],
            "patch": {"content": "repaired"},
        },
        lambda t: {"action": "inspect", "event_id": "x"},
        lambda t: {
            "action": "verdict",
            "review": report(t.payload, "fail" if failure == "negative_verdict" else "pass", ["x"]),
        },
    ]
    if failure == "context_limit":
        original = fake.__call__

        async def limited(t):
            if len(fake.calls) == 2:
                fake.calls.append(t)
                return {"context_limit": True}
            return await original(t)

        gen.transport = limited
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.events["x"].content == "repaired"
    assert source.metadata["stage"] == (-3 if failure else 1)
    assert len(fake.calls) == 3
    assert all(t.endpoint == "f" for t in fake.calls)
    assert len(fake.calls[1].payload["messages"]) > len(fake.calls[0].payload["messages"])
    history = gen.bulk.history(job)
    assert len(history) == 1 and history[0]["before"]["events"]["x"]["content"] == "before"
    restored = gen.bulk.restore(history)
    gen.bulk.restore(restored["deltas"], undo=False)
    assert len(fake.calls) == 3  # Redo never invokes model.


@pytest.mark.parametrize("repaired", [False, True])
@pytest.mark.parametrize("paused", [False, True])
def test_manual_detach_settles_progress_history_and_fences_late_result(tmp_path, repaired, paused):
    gen, _ = service(tmp_path)
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan()["windows"],
            events={"x": dict(kind="agent_text", window="reason", content="before")},
            metadata={"stage": -1},
        )
    )
    ident = record.source.id
    job = gen.start_review({}, trajectory=ident, expected_revision=record.revision)
    gen._manual_admit(job)
    gen._prepare(job, ident)
    ticket = gen.jobs.claim("f")
    if repaired:
        gen.jobs.request_result(
            ticket,
            {
                "output": {
                    "action": "patch",
                    "event_id": "x",
                    "expected_source_hash": ticket.payload["source_hash"],
                    "patch": {"content": "repaired"},
                }
            },
        )
        gen.consume(ticket)
        gen._prepare(job, ident)
        ticket = gen.jobs.claim("f")
    current = gen.store.read(ident)
    if paused:
        gen.jobs.control(job, "pause")
    gen.jobs.detach(job, ident)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert gen.jobs.inspect(job)["progress"] == gen.jobs.inspect(job)["total"] == 1
    (item,) = gen.bulk.results(job)
    assert item["state"] == "detached" and "Detached" in item["warning"]
    assert ident not in gen._owners(job)
    assert gen.store.read(ident).source.metadata["stage"] == -1  # No invented verdict.
    with pytest.raises(Fenced):
        gen.jobs.request_result(
            ticket, {"output": {"action": "verdict", "review": report(ticket.payload)}}
        )
    with pytest.raises(Fenced):
        gen.jobs.accept(job, ticket.epoch, ident, "late", current.source, current.revision, {})
    assert gen.store.read(ident).commit_id == current.commit_id
    with pytest.raises(OwnershipConflict):
        gen.jobs.detach(job, ident)
    assert gen.jobs.inspect(job)["progress"] == 1
    history = gen.bulk.history(job)
    assert len(history) == int(repaired)
    if repaired:
        assert history[0]["before"]["events"]["x"]["content"] == "before"
        assert history[0]["after"]["events"]["x"]["content"] == "repaired"
        gen.bulk.restore(history)
        assert gen.store.read(ident).source.events["x"].content == "before"


def test_manual_detach_keeps_other_items_running_and_cannot_be_resettled(tmp_path):
    gen, _ = service(tmp_path)
    for _ in range(2):
        gen.store.create(
            dict(
                format="kidra-trajectory-graph/1",
                id=str(uuid4()),
                windows={},
                events={},
                metadata={"stage": 0},
            )
        )
    job = gen.start_review({}, repair=False)
    gen._manual_admit(job)
    first, second = gen._owners(job)
    gen._prepare(job, first)
    ticket = gen.jobs.claim("f")
    gen.jobs.detach(job, first)
    assert gen.jobs.inspect(job)["state"] == "running"
    assert gen.jobs.inspect(job)["progress"] == 1
    assert gen._owners(job) == [second]
    with pytest.raises(Fenced):
        gen.jobs.request_result(ticket, {"output": report(ticket.payload)})
    # A concurrent admission/driver failure must not overwrite the terminal item.
    with gen.store.connect() as db:
        item = next(i for i in gen.bulk.results(job) if i["trajectory_id"] == first)
        gen.bulk._settle_item(db, job, item["position"], "skipped", "late failure")
    gen._manual_admit(job)
    assert gen.jobs.inspect(job)["progress"] == 1
    assert (
        next(i for i in gen.bulk.results(job) if i["trajectory_id"] == first)["state"] == "detached"
    )
    gen.jobs.detach(job, second)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert gen.jobs.inspect(job)["progress"] == 2


@pytest.mark.asyncio
async def test_manual_force_staleness_skips_locks(tmp_path):
    gen, fake = service(tmp_path)
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows={},
            events={},
            metadata={"stage": 3},
        )
    )
    job = gen.start_review({})
    await finish(gen, job)
    assert gen.bulk.results(job)[0]["state"] == "skipped" and not fake.calls
    job = gen.start_review({}, mode="force_fast", repair=False)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.metadata["stage"] == 3 and source.metadata["evaluations"][-1]["tier"] == "fast"
    # Inspect Fast provenance directly; a higher stage cannot imply a Critical signoff.
    settings_value = gen.store.settings().settings
    assert source.metadata["evaluations"][-1]["checklists"] == reviews.identities(settings_value)
    current = gen.store.read(source.id)
    locked = gen.jobs.create_job("manual")
    gen.jobs.acquire(locked, source.id, current.revision)
    job = gen.start_review({}, mode="force_critical")
    await finish(gen, job)
    assert gen.bulk.results(job)[0]["state"] == "skipped"


@pytest.mark.asyncio
async def test_restart_manual_resume_discard_cancel(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    job = gen.start([{"seed": 137}], {"plan": plan()})
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    gen._prepare(job, ident)
    ticket = gen.jobs.claim("g")
    gen.jobs.request_result(ticket, {"output": {"content": "discarded"}})
    restarted_jobs = JobService(gen.store)
    restarted = GenerationService(
        restarted_jobs, SamplerService(restarted_jobs), BulkService(restarted_jobs), fake
    )
    assert restarted_jobs.inspect(job)["state"] == "paused"
    with pytest.raises(OwnershipConflict):
        gen.consume(ticket)
    restarted_jobs.control(job, "resume")
    await finish(restarted, job)
    assert "discarded" not in json.dumps(gen.store.read(ident).source.to_dict())


def test_rest_generation_and_manual_popup_contract(tmp_path):
    path = tmp_path / "api.sqlite"
    GraphStore.initialize(path, settings(fast=False))
    fake = Fake()
    app = create_studio_app(path, provider=fake)
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        response = client.post(
            "/api/v1/generation/runs",
            json={
                "requests": [{"seed": 137, "overrides": {"domain": "software_engineering"}}],
                "instructions": {"plan": plan()},
            },
        )
        assert response.status_code == 202, response.text
        job = response.json()["job_id"]
        import time

        for _ in range(200):
            status = client.get("/api/v1/jobs/" + job).json()
            if status["state"] == "completed":
                break
            time.sleep(0.01)
        assert status["state"] == "completed"
        a = client.get("/api/v1/jobs/" + job + "/attempts").json()[0]
        record = client.get("/api/v1/trajectories/" + a["trajectory_id"]).json()
        assert (
            record["source"]["metadata"]["proposal"]["proposal"]["sampling_provenance"][
                "requested_overrides"
            ]["domain"]
            == "software_engineering"
        )
        assert (
            client.post("/api/v1/reviews", json={"trajectory": a["trajectory_id"]}).status_code
            == 422
        )
        assert client.get("/api/v1/project").json()["generation_available"] is True
        mcp = client.post(
            "/api/mcp/call",
            json={
                "name": "review_graphs",
                "arguments": {
                    "trajectory": a["trajectory_id"],
                    "expected_revision": record["revision"],
                    "repair": False,
                },
            },
        )
        assert mcp.status_code == 200, mcp.text
        manual_job = mcp.json()["job_id"]
        for _ in range(200):
            if client.get("/api/v1/jobs/" + manual_job).json()["state"] == "completed":
                break
            time.sleep(0.01)
        assert (
            client.get("/api/v1/trajectories/" + a["trajectory_id"]).json()["source"]["metadata"][
                "stage"
            ]
            == 1
        )
        bulk = client.post(
            "/api/v1/bulk",
            json={"query": {"stage": 1}, "action": "review_repair", "config": {"repair": False}},
        )
        assert bulk.status_code == 202, bulk.text
        bulk_job = bulk.json()["job_id"]
        for _ in range(200):
            if client.get("/api/v1/jobs/" + bulk_job).json()["state"] == "completed":
                break
            time.sleep(0.01)
        assert (
            client.get("/api/v1/trajectories/" + a["trajectory_id"]).json()["source"]["metadata"][
                "stage"
            ]
            == 2
        )


@pytest.mark.asyncio
async def test_checklist_and_source_stale_without_automatic_review(tmp_path):
    gen, fake = service(tmp_path)
    job = gen.start([{"seed": 137}], {"plan": plan()})
    await finish(gen, job)
    record = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"])
    before = len(fake.calls)
    updated = gen.store.settings().settings.model_dump(mode="json")
    updated["review_checklists"]["critical"]["training_value"]["text"] = "Edited critical criterion"
    current = gen.store.settings()
    gen.store.update_settings(updated, current.revision)
    assert reviews.stale(record.source, gen.store.settings().settings)["reasons"] == [
        "critical_checklist_changed"
    ]
    updated["review_checklists"]["core"]["causal_evidence"]["text"] = "Edited core criterion"
    current = gen.store.settings()
    gen.store.update_settings(updated, current.revision)
    data = record.source.to_dict()
    data["events"]["think"]["content"] = "manual edit"
    changed = gen.store.replace(record.source.id, data, record.revision)
    assert set(reviews.stale(changed.source, gen.store.settings().settings)["reasons"]) == {
        "source_changed",
        "core_checklist_changed",
        "critical_checklist_changed",
    }
    assert changed.source.metadata["stage"] == 2 and len(fake.calls) == before


@pytest.mark.asyncio
async def test_transport_exhaustion_pause_and_cancel_no_quality_failure(tmp_path):
    gen, _ = service(tmp_path)
    called = asyncio.Event()

    async def outage(ticket):
        called.set()
        raise OSError("provider unavailable")

    gen.transport = outage
    job = gen.start([{"seed": 137}], {"plan": plan()})
    task = asyncio.create_task(gen.run(gen.jobs, job))
    await asyncio.wait_for(called.wait(), 2)
    for _ in range(100):
        if gen.jobs.inspect(job)["paused_endpoints"]:
            break
        await asyncio.sleep(0.01)
    a = gen.jobs.attempts(job)[0]
    assert gen.jobs.inspect(job)["paused_endpoints"] == ["g"]
    assert gen.store.read(a["trajectory_id"]).source.metadata["stage"] == -3
    gen.jobs.control(job, "cancel")
    await asyncio.wait_for(task, 2)
    assert gen.jobs.attempts(job)[0]["state"] == "detached"
    assert gen.store.read(a["trajectory_id"]).source.metadata["stage"] == -3
    with pytest.raises(OwnershipConflict):
        gen.jobs.control(job, "resume")


@pytest.mark.asyncio
async def test_automatic_fast_failure_repair_then_critical(tmp_path):
    gen, fake = service(tmp_path)
    fake.responses = [
        *({"content": "x"} for _ in range(4)),
        {"arguments": {"content": "reply"}},
        lambda t: report(t.payload, "fail"),
        lambda t: {"action": "verdict", "review": report(t.payload, "pass")},
    ]
    job = gen.start([{"seed": 137}], {"plan": plan()})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == 2
    assert [(r["tier"], r["operation"], r["verdict"]) for r in source.metadata["evaluations"]] == [
        ("fast", "review", "fail"),
        ("fast", "repair", "pass"),
        ("critical", "review", "pass"),
    ]


@pytest.mark.asyncio
async def test_chat_echo_resume_and_following_actor_visibility(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    p = plan()
    p["tasks"].append(
        dict(id="followup", envelope=dict(kind="agent_text", window="reason", after=["call"]))
    )
    job = gen.start([{"seed": 137}], {"plan": p})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.events["followup"].after == ["call:echo"]
    assert "Supported reply" in fake.calls[-1].payload["messages"][1]["content"]
    assert len([i for i in source.events if i.endswith(":echo")]) == 1


def test_all_user_machine_smoke_envelopes_offline(tmp_path):
    import runpy
    from pathlib import Path

    smoke = runpy.run_path(str(Path(__file__).parents[1] / "scripts/graph_local_llm_smoke.py"))
    gen, _ = service(tmp_path)
    proposal = gen.sampler.propose(seed=137)["proposal"]
    for case in smoke["CASES"]:
        prepared = prepare_plan(
            proposal, gen.store.settings().settings, {"plan": smoke["smoke_plan"](case)}
        )
        assert prepared["tasks"] and "content" not in prepared["tasks"][0]["envelope"]


@pytest.mark.asyncio
async def test_invalid_review_evidence_never_publishes_pass(tmp_path):
    gen, fake = service(tmp_path, format_retries=0)
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows={},
            events={},
            metadata={"stage": 0},
        )
    )
    fake.responses = [lambda t: report(t.payload, "pass", ["missing-event"])]
    job = gen.start_review({}, repair=False)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.metadata["stage"] == 0 and "evaluations" not in source.metadata
    assert gen.jobs.inspect(job)["state"] == "paused"


@pytest.mark.asyncio
async def test_manual_repair_structurally_broken_draft(tmp_path):
    gen, fake = service(tmp_path)
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan()["windows"],
            events={
                "x": dict(kind="agent_text", window="reason", content="before", after=["missing"])
            },
            metadata={"stage": -1},
        )
    )
    fake.responses = [
        lambda t: {
            "action": "patch",
            "event_id": "x",
            "expected_source_hash": t.payload["source_hash"],
            "patch": {"after": []},
        },
        lambda t: {"action": "verdict", "review": report(t.payload, "pass", ["x"])},
    ]
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    assert gen.store.read(record.source.id).source.metadata["stage"] == 1
    assert gen.store.read(record.source.id).source.events["x"].after == []


@pytest.mark.asyncio
async def test_json_tool_schema_mismatch_advisory_not_retry(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    p = plan()
    p["tasks"] = [p["tasks"][-1]]
    p["tasks"][0]["envelope"]["after"] = []
    fake.responses = [{"arguments": {"unexpected": "schema mismatch is advisory"}}]
    job = gen.start([{"seed": 137}], {"plan": p})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == 0 and len(fake.calls) == 1
    assert len(source.events) == 1  # No fabricated echo for missing content.


@pytest.mark.asyncio
async def test_openai_transport_mock_complete_invalid_and_context(monkeypatch):
    import httpx

    from trajectory_remix.graph.jobs import Ticket
    from trajectory_remix.graph.provider import OpenAITransport

    captured = []
    responses = [
        httpx.Response(
            200,
            json={
                "model": "actual",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"content":"complete"}'}}
                ],
            },
        ),
        httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": "DO_NOT_KEEP_PARTIAL"}}
                ]
            },
        ),
        httpx.Response(400, text="context_length_exceeded"),
    ]

    async def handle(request):
        captured.append((json.loads(request.content), dict(request.headers)))
        return responses.pop(0)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setenv("REMIX_API_KEY_FAKE_ENDPOINT", "fixture-key")
    payload = {
        "operation": "event",
        "endpoint_config": {"url": "http://127.0.0.1:1/v1", "model": "requested"},
        "messages": [{"role": "user", "content": "visible only"}],
        "schema": {"type": "object"},
        "private_envelope": "not sent",
    }
    ticket = Ticket("id", "job", "source", "fake-endpoint", "step", 1, "lease", payload)
    transport = OpenAITransport()
    assert await transport(ticket) == {
        "output": {"content": "complete"},
        "model": "actual",
        "usage": None,
    }
    assert (await transport(ticket))["invalid_output"] is True
    assert (await transport(ticket))["context_limit"] is True
    assert "private_envelope" not in captured[0][0]
    assert captured[0][1]["authorization"] == "Bearer fixture-key"
    assert "fixture-key" not in json.dumps(payload)


def test_local_checkpoint_and_admission_keep_original_epoch(tmp_path):
    from trajectory_remix.graph.jobs import Fenced

    gen, _ = service(tmp_path, fast=False)
    job = gen.start([{"seed": 137}], {"plan": plan()})
    epoch = gen.jobs.inspect(job)["epoch"]
    gen._admit(job, epoch)
    record = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"])
    cp = dict(
        serial=0,
        phase="event",
        index=0,
        errors=0,
        manual=False,
        _epoch=epoch,
        _revision=record.revision,
    )
    gen.jobs.control(job, "pause")
    gen.jobs.control(job, "resume")
    with pytest.raises(Fenced):
        gen._save(job, record.source, cp)
    with pytest.raises(Fenced):
        gen._admit(job, epoch)
    with pytest.raises(Fenced):
        gen._prepare(job, record.source.id, epoch)
    assert gen.store.read(record.source.id).revision == record.revision
