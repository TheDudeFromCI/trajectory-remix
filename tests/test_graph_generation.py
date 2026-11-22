"""Tiny deterministic handoff-06 checks; no real provider, compiler or benchmark."""

import asyncio
import copy
import json
import re
from uuid import uuid4

import compact_spec
import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph import reviews
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.flow import causal_view
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import Fenced, JobService, OwnershipConflict
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore, RevisionConflict
from trajectory_remix.graph.studio_api import create_studio_app


def settings(**options):
    if options.get("fast") is False:
        options.setdefault("critical", False)
    return dict(
        endpoints={
            k: dict(url="http://127.0.0.1:1/v1", model=k, retries=0) for k in ("g", "f", "c")
        },
        endpoint_roles={"draft": "g", "fast": "f", "critical": "c"},
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


DRAFT = "Working notes. " * 20


def spec(**changes):
    body = dict(
        tools=["chat", "read"],
        windows=[
            dict(id="room", purpose="chatroom", writable=False),
            dict(id="notes", purpose="notes", writable=True),
            dict(id="w1", purpose="reasoning", writable=True),
            dict(id="w2", purpose="tool", writable=True),
            dict(id="ack_request", purpose="tool", writable=True),
            dict(id="tell_request", purpose="tool", writable=True),
        ],
        events=[
            dict(id="ask", kind="user", window="room", after=[], content="Hi", speaker="Ana"),
            dict(
                id="ack", kind="call", window="ack_request", after=["ask"], tool="chat",
                arguments={"content": "Sure, checking now."}, close=True,
            ),
            dict(id="plan", kind="think", window="w1", after=["ack"], content="Read it.", close=True),
            dict(
                id="look", kind="call", window="w2", after=["plan"], tool="read",
                arguments={"path": "a.md"},
            ),
            dict(id="got", kind="result", window="w2", after=["look"], content="A says yes."),
            dict(id="note", kind="think", window="notes", after=["got"], content="A: yes."),
            dict(id="close_read", kind="think", window="w2", after=["note"], content="", close=True),
            dict(
                id="tell", kind="call", window="tell_request", after=["note"], tool="chat",
                arguments={"content": "A says yes."}, close=True,
            ),
        ],
    )  # fmt: skip
    body.update(changes)
    return body


def tangled_spec():
    body = spec()
    # The result and call write to one window without a causal order.
    body["events"][4].update(after=["plan"], call="look")
    return body


def chat_spec():
    """An Assembly reply that needs only the chat tool, which every project has."""
    return dict(
        tools=["chat"],
        windows=[
            dict(id="room", purpose="chatroom", writable=False),
            dict(id="w1", purpose="reasoning", writable=True),
            dict(id="ack_request", purpose="tool", writable=True),
        ],
        events=[
            dict(id="ask", kind="user", window="room", after=[], content="Hi", speaker="Ana"),
            dict(
                id="think", kind="think", window="w1", after=["ask"], content="Reply.", close=True
            ),
            dict(
                id="ack",
                kind="call",
                window="ack_request",
                after=["think"],
                tool="chat",
                arguments={"content": "Hello!"},
                close=True,
            ),
        ],
    )


def tool_message(*calls):
    return dict(
        role="assistant",
        content=None,
        tool_calls=[
            dict(
                id=f"fixture-{i}",
                type="function",
                function=dict(name=name, arguments=json.dumps(args)),
            )
            for i, (name, args) in enumerate(calls)
        ],
    )


def viewed_graph(payload):
    """The latest view_graph result in a native conversation, or None before one."""
    names, graph = {}, None
    for message in payload["messages"]:
        for call in message.get("tool_calls") or []:
            names[call["id"]] = call["function"]["name"]
        if message["role"] == "tool" and names.get(message["tool_call_id"]) == "view_graph":
            graph = json.loads(message["content"])
    return graph


def offered_tools(payload):
    """The project tool catalogue a Draft or Assembly user message lists."""
    text = payload["messages"][1]["content"]
    block = re.search(r"Project tools:\n```json\n(.*?)\n```", text, re.S)
    return json.loads(block.group(1))


def native_report(payload, event_ids, status="pass", graph=None):
    parameters = next(
        t["function"]["parameters"] for t in payload["tools"] if t["function"]["name"] == "signoff"
    )
    criteria = parameters["properties"]["items"]["properties"]
    items = {
        key: dict(status=status, evidence="Observed causal evidence.", event_ids=list(event_ids))
        for key in criteria
    }
    if "causal_claims" in criteria.get("core.causal_evidence", {}).get("properties", {}):
        graph = (graph or viewed_graph(payload) or {}).get("events", {})
        items["core.causal_evidence"]["causal_claims"] = (
            [dict(consumer_event_id="note", evidence_event_ids=["got"])]
            if "note" in graph and "got" in graph
            else []
        )
    return "signoff", dict(status="complete", summary="offline fixture", items=items)


def native_assembly(payload, ident, body=None, *, signoff=True):
    catalogue = offered_tools(payload)
    body = body or (spec() if "read" in catalogue else chat_spec())
    source = compact_spec.build(
        body, {"id": ident, "format": "kidra-trajectory-graph/1"}, catalogue, 0
    )
    calls = [("post_tool_schema", {"tool_ids": body["tools"]})]
    calls.extend(
        ("configure_window", {"window_id": window, **definition})
        for window, definition in source["windows"].items()
        if window != "tools"
    )
    calls.extend(
        ("create_event", {"event_id": event, **definition})
        for event, definition in source["events"].items()
        if event != "tools"
    )
    if signoff:
        calls.append(native_report(payload, list(source["events"])[-2:]))
    return tool_message(*calls)


class Fake:
    def __init__(self):
        self.calls = []
        self.responses = []

    def graph(self, ticket):
        """The reviewed graph, read from the store when service() attached one."""
        store = getattr(self, "store", None)
        if store is None:
            return None
        return {"events": dict.fromkeys(store.read(ticket.trajectory_id).source.events)}

    async def __call__(self, ticket):
        self.calls.append(copy.deepcopy(ticket))
        p = ticket.payload
        if self.responses:
            value = self.responses.pop(0)
            if callable(value):
                value = value(ticket)
            return {"message" if "tools" in p else "output": value, "model": "actual-fixture"}
        if "tools" in p:
            if p["operation"] == "assembly":
                message = native_assembly(p, ticket.trajectory_id)
            elif (graph := viewed_graph(p) or self.graph(ticket)) is None:
                message = tool_message(("view_graph", {}))
            else:
                message = tool_message(native_report(p, list(graph["events"])[-1:], graph=graph))
            return {"message": message, "model": "actual-fixture"}
        assert p["operation"] == "draft", "only Draft uses a JSON reply"
        return {"output": {"draft": DRAFT}, "model": "actual-fixture"}


def service(tmp_path, fake=None, **opts):
    store = GraphStore.initialize(tmp_path / "p.sqlite", settings(**opts))
    jobs = JobService(store, active_limit=2)
    sampler, bulk = SamplerService(jobs), BulkService(jobs)
    fake = fake or Fake()
    fake.store = store  # The default review reads its graph as view_graph would.
    return GenerationService(jobs, sampler, bulk, fake), fake


async def finish(gen, job):
    # This deadline catches hangs; it is not a performance acceptance threshold.
    # Adaptive episodes make many durable transitions and deliberately poll
    # between them. Shared CI runners can exceed five seconds (notably 3.13).
    await asyncio.wait_for(gen.run(gen.jobs, job), 30)
    assert gen.jobs.inspect(job)["state"] in {"completed", "paused", "cancelled"}


@pytest.mark.asyncio
async def test_generation_reviews_snapshots_echo_and_ownership(tmp_path, monkeypatch):
    # A compile-free guard covers generation, review and repairs transitively.
    from trajectory_remix.graph.compiler import GraphCompiler

    monkeypatch.setattr(
        GraphCompiler, "__init__", lambda *a, **k: pytest.fail("generation compiled")
    )
    gen, fake = service(tmp_path)
    job = gen.start([{"seed": 137}, {"seed": 138}], {})
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
        assert source.events["ack:echo"].chat.speaker == "agent"
        calls = [t.payload["operation"] for t in fake.calls if t.trajectory_id == source.id]
        assert calls[:2] == ["draft", "assembly"]
        assert [e["tier"] for e in source.metadata["evaluations"]] == ["fast", "critical"]
        assert source.metadata["evaluations"][0]["model"] == "actual-fixture"
        assert reviews.stale(source, gen.settings_for(job))["stale"] is False
        with pytest.raises(RevisionConflict):
            gen.jobs.acquire(gen.jobs.create_job("manual"), source.id, record.revision - 1)
    assert gen.jobs.inspect(job)["config"] == frozen
    assert all(t.payload["endpoint_config"]["model"] != "edited-later" for t in fake.calls)


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


@pytest.mark.asyncio
async def test_generation_format_failure_no_replacement(tmp_path):
    gen, fake = service(tmp_path, fast=False, format_retries=1)
    fake.responses = [{"wrong": "x"}, {"wrong": "x"}]
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    attempts = gen.jobs.attempts(job)
    source = gen.store.read(attempts[0]["trajectory_id"]).source
    assert len(fake.calls) == 2 and len(attempts) == 1 and attempts[0]["outcome"] == "fail"
    assert source.metadata["stage"] == -1 and source.events == {}


@pytest.mark.asyncio
async def test_malformed_review_pauses_without_verdict(tmp_path):
    gen, fake = service(tmp_path, format_retries=0)
    fake.responses = [
        {"draft": DRAFT},
        lambda t: native_assembly(t.payload, t.trajectory_id),
        {"items": {}},
    ]
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    a = gen.jobs.attempts(job)[0]
    source = gen.store.read(a["trajectory_id"]).source
    assert source.metadata["stage"] == 0 and "evaluations" not in source.metadata
    assert a["state"] == "active"
    gen.jobs.control(job, "resume")
    await finish(gen, job)
    assert gen.store.read(a["trajectory_id"]).source.metadata["stage"] == 2


@pytest.mark.asyncio
async def test_a_review_edits_in_one_session_and_records_one_verdict(tmp_path):
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
        tool_message(
            ("update_event", {"event_id": "x", "content": "repaired", "close_window": True})
        ),
        tool_message(("view_event", {"event_id": "x"})),
        lambda t: tool_message(native_report(t.payload, ["x"])),
    ]
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.events["x"].content == "repaired"
    assert source.metadata["stage"] == 1
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
            {"message": tool_message(("update_event", {"event_id": "x", "content": "repaired"}))},
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
            ticket, {"message": tool_message(native_report(ticket.payload, ["x"]))}
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
    job = gen.start_review({})
    gen._manual_admit(job)
    first, second = gen._owners(job)
    gen._prepare(job, first)
    ticket = gen.jobs.claim("f")
    gen.jobs.detach(job, first)
    assert gen.jobs.inspect(job)["state"] == "running"
    assert gen.jobs.inspect(job)["progress"] == 1
    assert gen._owners(job) == [second]
    with pytest.raises(Fenced):
        gen.jobs.request_result(
            ticket, {"message": tool_message(native_report(ticket.payload, []))}
        )
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
            windows={"notes": dict(purpose="notes", agent_writable=True)},
            events={"note": dict(kind="agent_text", window="notes", content="Ready")},
            metadata={"stage": 3},
        )
    )
    job = gen.start_review({})
    await finish(gen, job)
    assert gen.bulk.results(job)[0]["state"] == "skipped" and not fake.calls
    job = gen.start_review({}, mode="force_fast")
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
    job = gen.start([{"seed": 137}], {})
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
                "instructions": {},
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
            json={"query": {"stage": 1}, "action": "review_repair", "config": {}},
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
    job = gen.start([{"seed": 137}], {})
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
    data["events"]["plan"]["content"] = "manual edit"
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
    job = gen.start([{"seed": 137}], {})
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
async def test_a_failing_signoff_keeps_the_review_fixing_then_critical(tmp_path):
    gen, fake = service(tmp_path)
    fake.responses = [
        {"draft": DRAFT},
        lambda t: native_assembly(t.payload, t.trajectory_id),
        lambda t: tool_message(native_report(t.payload, ["plan"], "fail")),
        lambda t: tool_message(native_report(t.payload, ["plan"])),
    ]
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == 2
    assert [(r["tier"], r["operation"], r["verdict"]) for r in source.metadata["evaluations"]] == [
        ("fast", "review", "pass"),
        ("critical", "review", "pass"),
    ]
    refused = json.loads(fake.calls[3].payload["messages"][-2]["content"])
    assert refused["accepted"] is False and "Continue fixing" in refused["error"]
    assert all(
        "signoff" in [t["function"]["name"] for t in c.payload["tools"]] for c in fake.calls[1:]
    )


@pytest.mark.asyncio
async def test_an_unfeasible_review_records_a_failed_verdict(tmp_path):
    gen, fake = service(tmp_path, critical=False)
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows=plan()["windows"],
            events={"x": dict(kind="agent_text", window="reason", content="before")},
            metadata={"stage": 0},
        )
    )
    unfeasible = ("signoff", dict(status="unfeasible", summary="The episode has no request."))
    fake.responses = [
        tool_message(("update_event", {"event_id": "x", "content": "after"}), unfeasible)
    ]
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.metadata["stage"] == -1 and source.events["x"].content == "after"
    (evaluation,) = source.metadata["evaluations"]
    assert evaluation["verdict"] == "fail" and evaluation["unfeasible"] is True
    assert evaluation["summary"] == "The episode has no request."


@pytest.mark.asyncio
async def test_invalid_review_evidence_never_publishes_pass(tmp_path):
    gen, fake = service(tmp_path, max_authoring_exchanges=1)
    record = gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows={},
            events={},
            metadata={"stage": 0},
        )
    )
    fake.responses = [lambda t: tool_message(native_report(t.payload, ["missing-event"]))]
    job = gen.start_review({})
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.metadata["stage"] == 0 and "evaluations" not in source.metadata
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert source.metadata["review_outcomes"][-1]["outcome"] == "exchange_limit"


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
        tool_message(("update_event", {"event_id": "x", "after": [], "close_window": True})),
        lambda t: tool_message(native_report(t.payload, ["x"])),
    ]
    job = gen.start_review({}, trajectory=record.source.id, expected_revision=record.revision)
    await finish(gen, job)
    assert gen.store.read(record.source.id).source.metadata["stage"] == 1
    assert gen.store.read(record.source.id).source.events["x"].after == []


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
    job = gen.start([{"seed": 137}], {})
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
