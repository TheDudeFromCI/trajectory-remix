"""Request validation errors must not echo large submitted bodies back to the dashboard."""

from fastapi.testclient import TestClient

from trajectory_remix.graph.request_errors import summarize
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


def test_summarize_drops_input_and_context():
    errors = [{"type": "value_error", "loc": ["body"], "msg": "bad", "input": "x" * 10, "ctx": {}}]
    assert summarize(errors) == [{"type": "value_error", "loc": ["body"], "msg": "bad"}]


def test_invalid_tokenizer_profile_error_omits_definition(tmp_path):
    path = tmp_path / "graph.sqlite"
    GraphStore.initialize(path)
    app = create_studio_app(path, artifact_root=tmp_path / "exports")
    merges = [[f"a{i}", f"b{i}"] for i in range(20000)]
    definition = {"model": {"type": "BPE", "vocab": {}, "merges": merges}, "added_tokens": []}
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        current = client.get("/api/v1/settings").json()
        settings = dict(current["settings"])
        settings["tokenizer_profiles"] = {
            "big": {"name": "big", "definition": definition, "special_tokens": {}}
        }
        response = client.put(
            "/api/v1/settings",
            json={"settings": settings, "expected_revision": current["revision"]},
        )
    assert response.status_code == 422
    assert len(response.content) < 2000
    (error,) = response.json()["detail"]
    assert "Missing required controls" in error["msg"]
    assert "input" not in error
