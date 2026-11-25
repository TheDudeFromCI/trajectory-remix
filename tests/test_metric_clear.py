"""Editor Metrics tab API: manual values can be set, then cleared back to unavailable."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


@pytest.fixture
def client(tmp_path):
    GraphStore.initialize(tmp_path / "g.sqlite")
    app = create_studio_app(tmp_path / "g.sqlite", artifact_root=tmp_path / "exports")
    with TestClient(app) as c:
        c.headers["X-Remix-Token"] = app.state.csrf
        yield app, c


def _create(app):
    src = GraphSource(
        format="kidra-trajectory-graph/1",
        id=str(uuid4()),
        windows={"w": {"purpose": "reasoning", "agent_writable": True}},
        events={"a": {"kind": "agent_text", "window": "w", "content": "a"}},
        metadata={"stage": 1},
    )
    app.state.metrics.store.create(src)
    return src.id


def test_set_then_clear_manual_metric(client):
    app, c = client
    ident = _create(app)
    path = f"/api/v1/metrics/trajectories/{ident}/lifecycle"
    choice = c.get(f"/api/v1/metrics/trajectories/{ident}").json()["values"]["lifecycle"]
    value = choice["definition"]["choices"][0]
    put = c.put(path, json={"expected_revision": 1, "value": value})
    assert put.status_code == 200, put.text
    assert put.json()["values"]["lifecycle"]["status"] == "available"
    assert put.json()["revision"] == 2
    delta = put.json()["delta"]
    assert delta["before"]["metadata"].get("metrics", {}) == {}
    assert delta["after"]["metadata"]["metrics"]["lifecycle"]["value"] == value
    assert delta["expected_revision"] == 2

    undo = c.post("/api/v1/bulk/history", json={"deltas": [delta], "undo": True})
    assert undo.status_code == 200, undo.text
    view = c.get(f"/api/v1/metrics/trajectories/{ident}").json()
    assert view["values"]["lifecycle"]["status"] == "unavailable" and view["revision"] == 3
    redo = c.post("/api/v1/bulk/history", json={"deltas": undo.json()["deltas"], "undo": False})
    assert redo.status_code == 200, redo.text
    assert c.get(f"/api/v1/metrics/trajectories/{ident}").json()["revision"] == 4

    stale = c.request("DELETE", path, json={"expected_revision": 1})
    assert stale.status_code == 409

    cleared = c.request("DELETE", path, json={"expected_revision": 4})
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["values"]["lifecycle"]["status"] == "unavailable"
    assert body["revision"] == 5 and body["delta"]["after"]["metadata"]["metrics"] == {}
    saved = app.state.metrics.store.read(ident).source.metadata
    assert "lifecycle" not in saved.get("metrics", {})


def test_clear_rejects_unknown_metric(client):
    app, c = client
    ident = _create(app)
    r = c.request(
        "DELETE", f"/api/v1/metrics/trajectories/{ident}/nope", json={"expected_revision": 1}
    )
    assert r.status_code in (400, 422)
