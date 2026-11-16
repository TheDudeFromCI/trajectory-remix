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


def test_clear_cancelled_job_removes_record_and_logs(client):
    job = client.jobs.create_job("log_export", total=1)
    client.jobs.append_log(job, "hello")
    assert client.delete(f"/api/v1/jobs/{job}").status_code == 409  # still running
    client.post(f"/api/v1/jobs/{job}/cancel")
    assert client.delete(f"/api/v1/jobs/{job}").json() == dict(cleared=True, job_id=job)
    assert client.get(f"/api/v1/jobs/{job}").status_code == 404
    assert client.delete(f"/api/v1/jobs/{job}").status_code == 404
