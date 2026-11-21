"""Clearing finished jobs."""

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph import GraphStore
from trajectory_remix.graph.job_api import create_job_app


@pytest.fixture
def client(tmp_path):
    store = GraphStore.initialize(tmp_path / "project.sqlite", dict(endpoints={}))
    app = create_job_app(store.path, artifact_root=tmp_path / "artifacts")
    with TestClient(app) as c:
        c.jobs = app.state.worker.jobs
        yield c


def test_clear_job_with_opened_phase_gate(client):
    from trajectory_remix.graph import phase_barrier

    job = client.jobs.create_job("log_export", total=1)
    phase_barrier.advance(client.jobs.store, job)
    client.post(f"/api/v1/jobs/{job}/cancel")
    assert client.delete(f"/api/v1/jobs/{job}").json() == dict(cleared=True, job_id=job)
    assert phase_barrier.phase(client.jobs.store, job) == 0


def test_clear_cancelled_job_removes_record_and_logs(client):
    job = client.jobs.create_job("log_export", total=1)
    client.jobs.append_log(job, "hello")
    assert client.delete(f"/api/v1/jobs/{job}").status_code == 409  # still running
    client.post(f"/api/v1/jobs/{job}/cancel")
    assert client.delete(f"/api/v1/jobs/{job}").json() == dict(cleared=True, job_id=job)
    assert client.get(f"/api/v1/jobs/{job}").status_code == 404
    assert client.delete(f"/api/v1/jobs/{job}").status_code == 404
