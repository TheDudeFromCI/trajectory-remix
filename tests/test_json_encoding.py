"""API responses keep dict keys that start with "_sa" (FastAPI drops them by default)."""

import uuid

from fastapi.testclient import TestClient

from trajectory_remix.graph.json_encoding import encode
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app

KEPT = {"_sa": 0, "_sample": 1, "_save": {"_salt": 2}, "sample": 3}


def test_encode_keeps_sa_keys():
    assert encode({"nested": [KEPT]}) == {"nested": [KEPT]}


def test_trajectory_responses_keep_sa_keys(tmp_path):
    GraphStore.initialize(tmp_path / "p.sqlite")
    app = create_studio_app(tmp_path / "p.sqlite", artifact_root=tmp_path / "exports")
    source = dict(
        format="kidra-trajectory-graph/1",
        id=str(uuid.uuid4()),
        windows={"doc": dict(purpose="document", agent_writable=True)},
        events={"a": dict(kind="external_text", window="doc", content="hi")},
        metadata=KEPT,
    )
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        assert client.post("/api/v1/trajectories", json=source).status_code == 201
        read = client.get(f"/api/v1/trajectories/{source['id']}").json()
    assert read["source"]["metadata"] == KEPT
