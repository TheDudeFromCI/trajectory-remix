"""Overlapping stale saves list every clashing value and can be saved keeping mine or theirs."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.merge import MergeConflict, merge
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


def test_merge_lists_every_conflict_with_each_side():
    base = {"a": 1, "b": {"x": 1}, "c": 1}
    ours = {"a": 2, "b": {"x": 2}}
    theirs = {"a": 3, "b": {"x": 3}, "c": 2}
    with pytest.raises(MergeConflict) as error:
        merge(base, ours, theirs)
    assert error.value.conflicts == [
        {"path": ["a"], "base": 1, "ours": 2, "theirs": 3},
        {"path": ["b", "x"], "base": 1, "ours": 2, "theirs": 3},
        {"path": ["c"], "base": 1, "theirs": 2},  # Ours removed it: no "ours" key.
    ]
    assert "and 2 more" in str(error.value)


def test_merge_applies_resolutions():
    base, ours, theirs = {"a": 1, "c": 1, "d": 1}, {"a": 2, "d": 2}, {"a": 3, "c": 2, "d": 1}
    keep = {("a",): "theirs", ("c",): "ours"}
    assert merge(base, ours, theirs, keep) == {"a": 3, "d": 2}


def _source(ident, content="a", label="x", stage=1):
    return GraphSource(
        format="kidra-trajectory-graph/1",
        id=ident,
        windows={"w": {"purpose": "reasoning", "agent_writable": True}},
        events={"a": {"kind": "agent_text", "window": "w", "content": content}},
        metadata={"stage": stage, "label": label},
    ).to_dict()


@pytest.fixture
def stale(tmp_path):
    """A trajectory saved elsewhere (content and stage changed) after this client loaded it."""
    GraphStore.initialize(tmp_path / "g.sqlite")
    app = create_studio_app(tmp_path / "g.sqlite", artifact_root=tmp_path / "exports")
    with TestClient(app) as c:
        c.headers["X-Remix-Token"] = app.state.csrf
        ident = str(uuid4())
        store = app.state.metrics.store
        store.create(_source(ident))
        store.replace(ident, _source(ident, content="theirs", stage=2), 1)
        yield c, ident


def _put(c, ident, **body):
    mine = _source(ident, content="mine", label="mine")
    return c.put(
        f"/api/v1/trajectories/{ident}", json={"expected_revision": 1, "source": mine} | body
    )


def test_conflict_response_carries_values(stale):
    c, ident = stale
    response = _put(c, ident)
    assert response.status_code == 409
    body = response.json()
    assert body["conflicts"] == [
        {"path": ["events", "a", "content"], "base": "a", "ours": "mine", "theirs": "theirs"}
    ]
    assert "choose which version to keep" in body["message"]


@pytest.mark.parametrize("keep", ["ours", "theirs"])
def test_resolved_save_keeps_chosen_side_and_both_other_changes(stale, keep):
    c, ident = stale
    resolutions = [{"path": ["events", "a", "content"], "keep": keep}]
    response = _put(c, ident, resolutions=resolutions)
    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert source["events"]["a"]["content"] == ("mine" if keep == "ours" else "theirs")
    assert source["metadata"] == {"stage": 2, "label": "mine"}  # Theirs and mine, both kept.


def test_resolutions_for_other_paths_do_not_hide_a_conflict(stale):
    c, ident = stale
    response = _put(c, ident, resolutions=[{"path": ["metadata", "label"], "keep": "ours"}])
    assert response.status_code == 409
    assert [x["path"] for x in response.json()["conflicts"]] == [["events", "a", "content"]]


def test_bad_resolution_is_rejected(stale):
    c, ident = stale
    response = _put(c, ident, resolutions=[{"path": ["events"], "keep": "both"}])
    assert response.status_code == 422
