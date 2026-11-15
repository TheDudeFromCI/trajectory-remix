"""Small deterministic runtime fixtures. No provider, benchmark or training calls."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph import GraphCommands, GraphStore, RevisionConflict
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.jobs import Fenced, JobService, OwnershipConflict


def source(ident=None, stage=0):
    return dict(
        format="kidra-trajectory-graph/1",
        id=ident or str(uuid4()),
        windows={"w": dict(purpose="reasoning", agent_writable=True)},
        events={"e": dict(kind="agent_text", window="w", content="a", close_window=True)},
        metadata=dict(stage=stage, split="Auto"),
    )


@pytest.fixture
def runtime(tmp_path):
    store = GraphStore.initialize(
        tmp_path / "project.sqlite",
        dict(
            endpoints={
                "a": dict(url="http://same.invalid", model="fake", concurrency=1, retries=1),
                "b": dict(url="http://same.invalid", model="fake", concurrency=1, retries=0),
            }
        ),
    )
    return store, JobService(store, active_limit=2)


def run(jobs, count=1):
    return jobs.create_run(dict(instructions="original"), [dict(seed=n) for n in range(count)])


def start(jobs, job, n=0):
    a = jobs.attempts(job)[n]
    return jobs.start_attempt(job, n, source(a["trajectory_id"]))


def queue(jobs, job, record, step="one", endpoint="a"):
    return jobs.enqueue(job, record.source.id, endpoint, step, dict(prompt="original"))


def test_frozen_run_and_quota(runtime):
    store, jobs = runtime
    config, proposals = dict(instructions="old"), [dict(seed=1), dict(seed=2), dict(seed=3)]
    job = jobs.create_run(config, proposals)
    config["instructions"] = "changed"
    proposals[2]["seed"] = 9
    assert jobs.inspect(job)["config"]["config"]["instructions"] == "old"
    assert jobs.attempts(job)[2]["proposal"]["seed"] == 3
    a, b = start(jobs, job), start(jobs, job, 1)
    with pytest.raises(OwnershipConflict):
        start(jobs, job, 2)
    jobs.accept(job, 1, a.source.id, "done", a.source, 1, {}, outcome="fail")
    c = start(jobs, job, 2)
    jobs.accept(job, 1, b.source.id, "done2", b.source, 1, {}, outcome="pass")
    jobs.accept(job, 1, c.source.id, "done3", c.source, 1, {}, outcome="pass")
    assert jobs.inspect(job)["state"] == "completed"
    assert jobs.inspect(job)["progress"] == 3
    assert [a["outcome"] for a in jobs.attempts(job)] == ["fail", "pass", "pass"]
    with pytest.raises(OwnershipConflict):
        start(jobs, job)
    # Detached identity can never become a new attached attempt/source.
    with pytest.raises(ValueError):
        store.create(a.source)


def test_fifo_retry_stays_current_and_other_endpoint_continues(runtime):
    _, jobs = runtime
    j1, j2 = run(jobs), run(jobs)
    a, b = start(jobs, j1), start(jobs, j2)
    first = queue(jobs, j1, a)
    queue(jobs, j2, b)
    third = queue(jobs, j1, a, "two")
    other = queue(jobs, j2, b, "other", "b")
    t = jobs.claim("a")
    assert t.id == first
    assert jobs.claim("a") is None
    assert jobs.retry(t)
    assert jobs.claim("a") is None
    assert not jobs.retry(t)
    assert jobs.claim("a") is None
    assert jobs.claim("b").id == other
    assert jobs.inspect(j1)["paused_endpoints"] == ["a"]
    assert jobs.inspect(j1)["state"] == "running"
    jobs.resume_endpoint("a")
    t = jobs.claim("a")
    assert t.id == first
    jobs.request_result(t, dict(text="complete"))
    jobs.accept(j1, t.epoch, a.source.id, "one", a.source, 1, {}, request=t)
    # b is busy on its independent endpoint; strict head is not overtaken.
    assert jobs.claim("a") is None
    jobs.detach(j2, b.source.id)
    assert jobs.claim("a").id == third


def test_atomic_idempotent_acceptance_and_ownership(runtime):
    store, jobs = runtime
    job = run(jobs)
    a = start(jobs, job)
    with pytest.raises(OwnershipConflict):
        GraphCommands(store).patch(a.source.id, {"metadata": {"stage": 3}}, 1)
    manual = jobs.create_job("review")
    with pytest.raises(OwnershipConflict):
        jobs.acquire(manual, a.source.id, 1)
    queue(jobs, job, a)
    t = jobs.claim("a")
    jobs.request_result(t, dict(text="complete"))
    draft = a.source.to_dict()
    draft["events"]["e"]["content"] = "accepted"
    committed = jobs.accept(job, 1, a.source.id, "one", draft, 1, {"next": 2}, request=t)
    assert jobs.accept(job, 1, a.source.id, "one", draft, 1, {"next": 2}, request=t) == committed
    assert store.read(a.source.id).revision == 2
    assert jobs.inspect(job)["checkpoint"] == {"next": 2}
    assert jobs.inspect(job)["requests"] == [{"state": "accepted", "count": 1}]
    with pytest.raises(RevisionConflict):
        jobs.accept(job, 1, a.source.id, "two", draft, 1, {"next": 3})
    assert jobs.inspect(job)["checkpoint"] == {"next": 2}
    assert store.read(a.source.id).revision == 2
    jobs.detach(job, a.source.id)
    epoch = jobs.acquire(manual, a.source.id, 2)
    second = jobs.create_job("repair")
    with pytest.raises(OwnershipConflict):
        jobs.acquire(second, a.source.id, 2)
    jobs.accept(manual, epoch, a.source.id, "review", draft, 2, {}, outcome="pass")
    assert store.read(a.source.id).revision == 3


@pytest.mark.parametrize("action", ["pause", "cancel", "detach", "restart"])
def test_late_response_fence_and_restart_manual(runtime, action):
    store, jobs = runtime
    job = run(jobs, 2)
    a = start(jobs, job)
    queue(jobs, job, a)
    t = jobs.claim("a")
    if action == "detach":
        jobs.detach(job, a.source.id)
    elif action == "restart":
        jobs = JobService(store)
    else:
        jobs.control(job, action)
    with pytest.raises(Fenced):
        jobs.request_result(t, {"text": "late"})
    with pytest.raises(Fenced):
        jobs.accept(job, t.epoch, a.source.id, "late", a.source, 1, {})
    assert store.read(a.source.id).revision == 1
    if action in {"pause", "restart"}:
        assert jobs.claim("a") is None
        jobs.control(job, "resume")
        newer = jobs.claim("a")
        assert newer.id == t.id and newer.epoch > t.epoch
        assert newer.payload == t.payload
    if action == "cancel":
        assert jobs.inspect(job)["state"] == "cancelled"
        assert jobs.attempts(job)[0]["outcome"] == "cancelled"
        assert jobs.attempts(job)[1]["state"] == "cancelled"
        assert store.read(a.source.id).source.metadata["stage"] == 0
        # Committed source is now editable, cancellation is no quality verdict.
        GraphCommands(store).patch(a.source.id, {"metadata": {"note": "kept"}}, 1)


async def test_driver_retry_discards_partial_and_original_inputs(runtime):
    store, jobs = runtime
    job = run(jobs)
    a = start(jobs, job)
    queue(jobs, job, a)
    seen = []

    async def fake(ticket):
        seen.append(ticket.payload.copy())
        ticket.payload["prompt"] = "mutated local"
        if len(seen) == 1:
            partial = "incomplete-secret-body"
            raise RuntimeError(partial)
        return {"text": "whole response"}

    ticket = await jobs.execute_one("a", fake)
    assert seen == [{"prompt": "original"}, {"prompt": "original"}]
    with store.connect() as db:
        r = db.execute("SELECT * FROM graph_requests WHERE id=?", (ticket.id,)).fetchone()
        assert r["retries"] == 1
        assert json.loads(r["result"]) == {"text": "whole response"}
        assert "incomplete-secret-body" not in json.dumps(dict(r))
    assert jobs.logs(job)["items"] == []


def test_endpoint_caps_and_single_trajectory_slot(runtime):
    _, jobs = runtime
    with jobs.store.connect() as db:
        db.execute("UPDATE graph_endpoints SET concurrency=2 WHERE id='a'")
    job = run(jobs, 2)
    a, b = start(jobs, job), start(jobs, job, 1)
    q1 = queue(jobs, job, a)
    q2 = queue(jobs, job, b, "two")
    queue(jobs, job, a, "three", "b")
    assert jobs.claim("a").id == q1
    assert jobs.claim("a").id == q2
    assert jobs.claim("a") is None
    assert jobs.claim("b") is None


def test_log_budget_clear_and_job_isolation(runtime):
    _, jobs = runtime
    a, b = jobs.create_job("a"), jobs.create_job("b")
    jobs.append_log(a, "aa", budget=4)
    jobs.append_log(b, "bb", budget=4)
    jobs.append_log(a, "cc", budget=4)
    assert jobs.logs(a)["truncated"]
    assert [x["text"] for x in jobs.logs(a)["items"]] == ["cc"]
    assert [x["text"] for x in jobs.logs(b)["items"]] == ["bb"]
    jobs.clear_logs(a)
    jobs.append_log(a, "d", budget=4)
    assert [x["text"] for x in jobs.logs(a)["items"]] == ["d"]
    assert jobs.inspect(a)["state"] == "running"


def test_bulk_all_matches_revision_lock_skips_and_undo_redo(runtime):
    store, jobs = runtime
    records = [store.create(source()) for _ in range(3)]
    bulk = BulkService(jobs)
    locked = jobs.create_job("manual")
    jobs.acquire(locked, records[0].source.id, 1)
    job = bulk.start({"stage": 0}, "stage", {"value": 1})
    store.replace(records[1].source.id, source(records[1].source.id, stage=3), 1)
    later = store.create(source())
    assert bulk.advance(job, batch_size=1)["progress"] == 1
    while jobs.inspect(job)["state"] == "running":
        bulk.advance(job, batch_size=1)
    results = bulk.results(job)
    assert len(results) == 3
    assert sorted(r["state"] for r in results) == ["changed", "skipped", "skipped"]
    assert store.read(later.source.id).source.metadata["stage"] == 0
    history = bulk.history(job)
    assert len(history) == 1
    undo = bulk.restore(history)
    assert undo["warnings"] == []
    ident = history[0]["id"]
    assert store.read(ident).source.metadata["stage"] == 0
    redo = bulk.restore(undo["deltas"], undo=False)
    assert store.read(ident).source.metadata["stage"] == 1
    GraphCommands(store).patch(
        ident, {"metadata": {"note": "intervening"}}, store.read(ident).revision
    )
    assert len(bulk.restore(redo["deltas"])["warnings"]) == 1


def test_delete_undo_redo_keeps_immutable_history_and_monotone_revision(runtime):
    store, jobs = runtime
    a = store.create(source())
    bulk = BulkService(jobs)
    job = bulk.start({}, "delete")
    bulk.advance(job)
    with pytest.raises(KeyError):
        store.read(a.source.id)
    assert store.read(a.source.id, 1) == a
    with pytest.raises(ValueError):
        store.create(a.source)
    undo = bulk.restore(bulk.history(job))
    assert store.read(a.source.id).revision == 2
    redo = bulk.restore(undo["deltas"], undo=False)
    with pytest.raises(KeyError):
        store.read(a.source.id)
    bulk.restore(redo["deltas"])
    assert store.read(a.source.id).revision == 3


def test_bulk_cancel_partial_history_metrics_redo_never_calls_handler(runtime):
    store, jobs = runtime
    for _ in range(3):
        store.create(source())
    bulk = BulkService(jobs)
    calls = []

    def metrics(record, options):
        calls.append(record.source.id)
        return {"known": dict(value=7, source_revision=record.revision)}

    job = bulk.start({}, "metrics")
    bulk.advance(job, {"metrics": metrics}, batch_size=1)
    with pytest.raises(OwnershipConflict):
        bulk.history(job)
    jobs.control(job, "cancel")
    deltas = bulk.history(job)
    assert len(deltas) == 1
    undo = bulk.restore(deltas)
    bulk.restore(undo["deltas"], undo=False)
    assert len(calls) == 1


def test_bulk_handler_cancel_during_work_is_fenced(runtime):
    store, jobs = runtime
    a = store.create(source())
    bulk = BulkService(jobs)
    job = bulk.start({}, "review_repair")

    def review(record, options):
        jobs.control(job, "cancel")
        result = record.source.to_dict()
        result["metadata"]["stage"] = 2
        return result

    bulk.advance(job, {"review_repair": review})
    assert store.read(a.source.id).revision == 1
    assert bulk.history(job) == []


def test_change_cursor_gap_is_explicit(runtime):
    store, jobs = runtime
    with store.connect() as db:
        db.execute("DELETE FROM graph_project_changes")
        db.execute(
            "INSERT INTO graph_project_changes(cursor,kind,entity_id,revision,commit_id) VALUES(1005,'job','j',1,'c')"
        )
    assert jobs.changes(1)["resync"]
    assert not jobs.changes(1004)["resync"]


def test_export_job_pins_sources_and_log_artifact_expiry(runtime, tmp_path):
    from trajectory_remix.graph.export import ArtifactService
    from trajectory_remix.graph.job_artifacts import JobArtifacts

    store, jobs = runtime
    a = store.create(source(stage=3))
    clock = [100]
    artifacts = ArtifactService(tmp_path / "artifacts", clock=lambda: clock[0])
    service = JobArtifacts(jobs, artifacts)
    job = service.start_export({"compiled": False})
    GraphCommands(store).patch(a.source.id, {"metadata": {"stage": 0}}, 1)
    manifest = service.build_export(job)
    assert manifest["sources"][a.source.id]["revision"] == 1
    assert jobs.inspect(job)["state"] == "completed"
    jobs.append_log(job, "retained")
    logs = service.export_logs(job)
    jobs.clear_logs(job)
    with service.lease(logs, "logs.txt") as stream:
        assert stream.read() == b"retained\n"
        clock[0] += 86401
        assert artifacts.expire()  # export expires, leased logs stay alive
        assert stream.read() == b""
    artifacts.expire()
    with pytest.raises(FileNotFoundError):
        with service.lease(logs, "logs.txt"):
            pass
    assert jobs.inspect(logs)["state"] == "completed"


def test_api_background_bulk_controls_and_cursor_reads(runtime, tmp_path):
    from trajectory_remix.graph.job_api import create_job_app

    store, jobs = runtime
    store.create(source())
    app = create_job_app(store.path, artifact_root=tmp_path / "artifacts")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/bulk", json={"query": {}, "action": "stage", "config": {"value": 1}}
        )
        assert response.status_code == 202
        job = response.json()["job_id"]
        # Waiting on the owned task is deterministic, no timing/throughput asserts.
        client.portal.call(lambda: app.state.worker.tasks[job])
        assert client.get(f"/api/v1/jobs/{job}").json()["state"] == "completed"
        assert len(client.get(f"/api/v1/jobs/{job}/history").json()) == 1
        assert client.post(f"/api/v1/jobs/{job}/resume").status_code == 409
        assert client.get("/api/v1/jobs?limit=0").status_code == 422
        assert client.get("/api/v1/jobs/missing").status_code == 404
        assert (
            client.post(
                "/api/v1/bulk", json={"action": "stage", "config": {"value": 9}}
            ).status_code
            == 422
        )
        assert client.get("/api/v1/changes").json()["items"]


def test_parallel_claim_and_bulk_workers_do_not_duplicate_publications(runtime):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store, jobs = runtime
    job = run(jobs)
    a = start(jobs, job)
    queue(jobs, job, a)
    with ThreadPoolExecutor(2) as pool:
        tickets = list(pool.map(lambda _: jobs.claim("a"), range(2)))
    assert sum(t is not None for t in tickets) == 1
    jobs.control(job, "cancel")
    bulk = BulkService(jobs)
    bulk_job = bulk.start({}, "metrics")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def metrics(record, options):
        calls.append(record.source.id)
        entered.set()
        assert release.wait(5)
        return {"count": 1}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(bulk.advance, bulk_job, {"metrics": metrics})
        assert entered.wait(5)
        # A second worker must not steal the same entry or complete the job.
        result = pool.submit(bulk.advance, bulk_job, {"metrics": metrics}).result()
        assert result["state"] == "running"
        assert result["progress"] == 0
        release.set()
        assert first.result()["state"] == "completed"
    assert calls == [a.source.id]
    assert store.read(a.source.id).revision == 2
    assert len(bulk.history(bulk_job)) == 1


def test_bulk_pause_resume_and_restart_keep_lock_and_original_selection(runtime):
    store, jobs = runtime
    a = store.create(source())
    bulk = BulkService(jobs)
    job = bulk.start({}, "metrics")

    def pause(record, options):
        jobs.control(job, "pause")
        return {"first": 1}

    bulk.advance(job, {"metrics": pause})
    assert store.read(a.source.id).revision == 1
    with pytest.raises(OwnershipConflict):
        GraphCommands(store).patch(a.source.id, {"metadata": {"stage": 3}}, 1)
    jobs = JobService(store)
    bulk = BulkService(jobs)
    assert jobs.inspect(job)["state"] == "paused"
    jobs.control(job, "resume")
    bulk.advance(job, {"metrics": lambda record, options: {"second": 2}})
    assert store.read(a.source.id).source.metadata["metrics"] == {"second": 2}
    assert jobs.inspect(job)["state"] == "completed"


async def test_cancel_interrupts_active_driver_and_fences_ignored_cancellation(runtime):
    store, jobs = runtime
    job = run(jobs)
    a = start(jobs, job)
    queue(jobs, job, a)
    entered = asyncio.Event()

    async def provider(ticket):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Simulate a transport that returns a late final body anyway.
            return {"text": "late"}

    task = asyncio.create_task(jobs.execute_one("a", provider))
    await entered.wait()
    jobs.control(job, "cancel")
    with pytest.raises(Fenced):
        await task
    assert jobs.inspect(job)["state"] == "cancelled"
    assert store.read(a.source.id).revision == 1
    assert jobs.logs(job)["items"] == []


def test_checkpoint_per_source_idempotency_and_no_transaction_around_driver(runtime):
    store, jobs = runtime
    job = run(jobs, 2)
    a, b = start(jobs, job), start(jobs, job, 1)
    jobs.accept(job, 1, a.source.id, "a1", a.source, 1, {"a": 1})
    jobs.accept(job, 1, b.source.id, "b1", b.source, 1, {"b": 1})
    assert jobs.checkpoint(job, a.source.id) == {"a": 1}
    assert jobs.checkpoint(job, b.source.id) == {"b": 1}
    with pytest.raises(ValueError):
        jobs.accept(job, 1, b.source.id, "a1", b.source, 1, {"b": 2})
    bulk = BulkService(jobs)
    detached = store.create(source())
    bulk_job = bulk.start({"text": detached.source.id}, "metrics")

    def metrics(record, options):
        # A second connection can write while expensive calculation is in flight.
        store.create(source())
        return {"metric": 1}

    bulk.advance(bulk_job, {"metrics": metrics})
    assert store.read(detached.source.id).source.metadata["metrics"] == {"metric": 1}


def test_export_cancel_during_publication_removes_orphan(runtime, tmp_path):
    from trajectory_remix.graph.export import ArtifactService
    from trajectory_remix.graph.job_artifacts import JobArtifacts

    store, jobs = runtime
    store.create(source(stage=3))
    artifacts = ArtifactService(tmp_path / "artifacts")
    service = JobArtifacts(jobs, artifacts)
    job = service.start_export({"compiled": False})
    real = artifacts.build

    def racing(*args, **kwargs):
        artifact, manifest = real(*args, **kwargs)
        jobs.control(job, "cancel")
        return artifact, manifest

    artifacts.build = racing
    with pytest.raises(Fenced):
        service.build_export(job)
    assert not list(artifacts.root.glob("export-*"))
    assert jobs.inspect(job)["artifact"] is None


def test_request_claim_lease_fences_old_ticket_after_endpoint_resume(runtime):
    _, jobs = runtime
    job = run(jobs)
    a = start(jobs, job)
    queue(jobs, job, a)
    old = jobs.claim("a")
    assert jobs.retry(old)
    assert not jobs.retry(old)
    jobs.resume_endpoint("a")
    new = jobs.claim("a")
    assert old.id == new.id and old.epoch == new.epoch and old.lease != new.lease
    with pytest.raises(Fenced):
        jobs.request_result(old, {"text": "stale"})
    jobs.request_result(new, {"text": "fresh"})
    assert jobs.result(new) == {"text": "fresh"}
    with pytest.raises(Fenced):
        jobs.accept(job, old.epoch, a.source.id, old.step, a.source, 1, {}, request=old)
    jobs.accept(job, new.epoch, a.source.id, new.step, a.source, 1, {}, request=new)


def test_api_exports_and_retained_log_downloads(runtime, tmp_path):
    from trajectory_remix.graph.job_api import create_job_app

    store, _ = runtime
    store.create(source(stage=3))
    app = create_job_app(store.path, artifact_root=tmp_path / "artifacts")
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/exports", json={"options": {"unknown": True}}).status_code == 422
        )
        response = client.post("/api/v1/exports", json={"options": {"compiled": False}})
        assert response.status_code == 202
        job = response.json()["job_id"]
        client.portal.call(lambda: app.state.worker.tasks[job])
        assert client.get(f"/api/v1/jobs/{job}").json()["state"] == "completed"
        download = client.get(f"/api/v1/jobs/{job}/artifacts/download.zip")
        assert download.status_code == 200 and download.content.startswith(b"PK")
        app.state.jobs.append_log(job, "one")
        log_job = client.post(f"/api/v1/logs/{job}/export").json()["job_id"]
        client.delete("/api/v1/logs", params={"job": job})
        assert client.get(f"/api/v1/jobs/{log_job}/artifacts/logs.txt").content == b"one\n"
        assert client.get("/api/v1/logs/usage").json()["bytes"] == 0
        assert client.get(f"/api/v1/jobs/{job}/artifacts/missing.txt").status_code == 404
        assert client.post("/api/v1/bulk/history", json={"deltas": [{}]}).status_code == 422


async def test_headless_job_handler_drives_two_calls_without_model_imports(runtime):
    from trajectory_remix.graph.job_api import JobWorker

    store, jobs = runtime
    job = run(jobs)
    seen = []

    async def fake(ticket):
        seen.append(ticket.step)
        return {"text": ticket.step}

    async def handler(service, ident):
        a = start(service, ident)
        for n in range(2):
            current = store.read(a.source.id)
            step = f"event-{n}"
            service.enqueue(ident, a.source.id, "a", step, {"event": n})
            ticket = await service.execute_one("a", fake)
            result = service.result(ticket)
            draft = current.source.to_dict()
            draft["events"]["e"]["content"] = result["text"]
            service.accept(
                ident,
                ticket.epoch,
                a.source.id,
                step,
                draft,
                current.revision,
                {"next": n + 1},
                request=ticket,
                outcome="pass" if n == 1 else None,
            )

    worker = JobWorker(jobs, BulkService(jobs), None, handlers={"run": handler})
    worker.kick(job)
    await worker.tasks[job]
    assert seen == ["event-0", "event-1"]
    assert jobs.inspect(job)["state"] == "completed"
    assert jobs.attempts(job)[0]["outcome"] == "pass"
    assert jobs.checkpoint(job, jobs.attempts(job)[0]["trajectory_id"]) == {"next": 2}
    await worker.close()


def test_existing_run_keeps_original_retry_budget_after_settings_edit(runtime):
    store, jobs = runtime
    job = run(jobs)
    a = start(jobs, job)
    settings = store.settings()
    draft = settings.settings.model_dump(mode="json")
    draft["endpoints"]["a"]["retries"] = 0
    store.update_settings(draft, settings.revision)
    jobs.configure_endpoints()
    queue(jobs, job, a)
    ticket = jobs.claim("a")
    assert jobs.retry(ticket)  # frozen run still has one retry
    assert not jobs.retry(ticket)
