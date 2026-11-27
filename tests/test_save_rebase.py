"""Stale saves are merged onto newer revisions when their changes don't overlap.

Trajectories, project settings (including review settings) and the sampler matrix share one
revision counter per entity, so an editor opened before another save used to fail with a
revision conflict even when it changed something else entirely.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.merge import MergeConflict, merge
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.storage import GraphStore, RevisionConflict
from trajectory_remix.graph.studio_api import create_studio_app


def test_merge_combines_separate_changes():
    base = {"a": 1, "b": {"x": 1, "y": 1}, "gone": 1}
    ours = {"a": 2, "b": {"x": 1, "y": 1}, "new": 1}
    theirs = {"a": 1, "b": {"x": 1, "y": 2}, "gone": 1}
    assert merge(base, ours, theirs) == {"a": 2, "b": {"x": 1, "y": 2}, "new": 1}


def test_merge_conflicts_on_same_value():
    with pytest.raises(MergeConflict, match="b/x"):
        merge({"b": {"x": 1}}, {"b": {"x": 2}}, {"b": {"x": 3}})
    with pytest.raises(MergeConflict):
        merge({"l": [1]}, {"l": [1, 2]}, {"l": [0, 1]})  # Lists are replaced whole.
    with pytest.raises(MergeConflict):
        merge({"e": {"k": 1}}, {}, {"e": {"k": 2}})  # Deleted on one side, edited on the other.


def _source(ident=None, content="a", stage=1):
    return GraphSource(
        format="kidra-trajectory-graph/1",
        id=ident or str(uuid4()),
        windows={"w": {"purpose": "reasoning", "agent_writable": True}},
        events={"a": {"kind": "agent_text", "window": "w", "content": content}},
        metadata={"stage": stage},
    ).to_dict()


@pytest.fixture
def client(tmp_path):
    GraphStore.initialize(tmp_path / "g.sqlite")
    app = create_studio_app(tmp_path / "g.sqlite", artifact_root=tmp_path / "exports")
    with TestClient(app) as c:
        c.headers["X-Remix-Token"] = app.state.csrf
        yield app, c


def test_stale_trajectory_save_keeps_newer_metadata(client):
    app, c = client
    src = _source()
    store = app.state.metrics.store
    store.create(src)
    store.replace(src["id"], _source(src["id"], stage=2), 1)  # e.g. a metric or job landed.
    saved = c.put(
        f"/api/v1/trajectories/{src['id']}",
        json={"expected_revision": 1, "source": _source(src["id"], content="edited")},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"] == 3
    assert body["source"]["events"]["a"]["content"] == "edited"
    assert body["source"]["metadata"]["stage"] == 2


def test_stale_trajectory_save_still_conflicts_on_same_field(client):
    app, c = client
    src = _source()
    store = app.state.metrics.store
    store.create(src)
    store.replace(src["id"], _source(src["id"], content="theirs"), 1)
    saved = c.put(
        f"/api/v1/trajectories/{src['id']}",
        json={"expected_revision": 1, "source": _source(src["id"], content="ours")},
    )
    assert saved.status_code == 409
    assert "events/a/content" in saved.json()["message"]
    assert store.read(src["id"]).source.events["a"].content == "theirs"


def test_store_replace_is_strict_unless_asked(tmp_path):
    store = GraphStore.initialize(tmp_path / "g.sqlite")
    src = _source()
    store.create(src)
    store.replace(src["id"], _source(src["id"], stage=2), 1)
    with pytest.raises(RevisionConflict):  # Jobs, bulk edits and Undo keep exact checks.
        store.replace(src["id"], _source(src["id"], content="x"), 1)


def test_settings_saved_after_sampler_edit(client):
    app, c = client
    opened = c.get("/api/v1/settings").json()  # Project/review settings dialog opens.
    editor = c.get("/api/v1/sampler").json()
    matrix = editor["configuration"]["matrix"]
    matrix["dimensions"]["user_tone"][0]["description"] = "Edited in sampler"
    sampled = c.put("/api/v1/sampler", json={"expected_revision": 1, "matrix": matrix})
    assert sampled.status_code == 200, sampled.text

    settings = opened["settings"]
    settings["log_cache_mib"] += 1
    saved = c.put("/api/v1/settings", json={"expected_revision": 1, "settings": settings})
    assert saved.status_code == 200, saved.text
    latest = c.get("/api/v1/sampler").json()["configuration"]["matrix"]
    assert latest["dimensions"]["user_tone"][0]["description"] == "Edited in sampler"
    assert c.get("/api/v1/settings").json()["revision"] == 3


def test_sampler_saved_after_settings_edit(client):
    app, c = client
    editor = c.get("/api/v1/sampler").json()  # Sampler opened at revision 1.
    settings = c.get("/api/v1/settings").json()["settings"]
    settings["log_cache_mib"] += 1
    first = c.put("/api/v1/settings", json={"expected_revision": 1, "settings": settings})
    assert first.status_code == 200, first.text
    matrix = editor["configuration"]["matrix"]
    matrix["dimensions"]["user_tone"][0]["description"] = "Late sampler edit"
    sampled = c.put("/api/v1/sampler", json={"expected_revision": 1, "matrix": matrix})
    assert sampled.status_code == 200, sampled.text
    assert (
        c.get("/api/v1/settings").json()["settings"]["log_cache_mib"] == settings["log_cache_mib"]
    )
