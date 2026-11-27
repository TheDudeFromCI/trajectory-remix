"""Settings tables, pruned snapshots and the upgrade from one-JSON-per-revision projects."""

import json
import sqlite3
import uuid

import pytest
from test_graph_compiler import profile

from trajectory_remix.graph import settings_history
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.models import canonical
from trajectory_remix.graph.storage import GraphStore, RevisionConflict


def source():
    return dict(
        format="kidra-trajectory-graph/1",
        id=str(uuid.uuid4()),
        windows={"doc": dict(purpose="document", agent_writable=True)},
        events={"a": dict(kind="external_text", window="doc", content="hello")},
    )


def configured():
    return {
        "tokenizer_profiles": {"bytes": profile().model_dump(mode="json")},
        "endpoints": {"local": {"url": "http://127.0.0.1:8000/v1", "model": "m"}},
        "endpoint_roles": {"draft": "local"},
    }


def save(store, revision, **changes):
    data = store.settings().settings.model_dump(mode="json") | changes
    return store.update_settings(data, revision).revision


def rows(store, sql):
    with store.connect() as db:
        return [tuple(row) for row in db.execute(sql)]


def revisions(store):
    return [r for (r,) in rows(store, "SELECT revision FROM graph_settings_revisions ORDER BY 1")]


def test_settings_live_in_tables_and_round_trip(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite", configured())
    expected = store.settings().settings.model_dump(mode="json")
    assert rows(store, "SELECT id FROM graph_tokenizer_profiles") == [("bytes",)]
    assert rows(store, "SELECT id FROM graph_settings_endpoints") == [("local",)]
    assert rows(store, "SELECT role,endpoint_id FROM graph_settings_endpoint_roles") == [
        ("draft", "local")
    ]
    assert rows(store, "SELECT id FROM graph_settings_tools") == [("chat",)]
    assert GraphStore(store.path).settings().settings.model_dump(mode="json") == expected
    snapshot = json.loads(rows(store, "SELECT settings FROM graph_settings_revisions")[0][0])
    assert "definition" not in snapshot["tokenizer_profiles"]["bytes"]


def test_saves_store_each_tokenizer_once_and_prune_unused_snapshots(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite", configured())
    revision = 1
    for i in range(settings_history.RECENT + 10):
        revision = save(store, revision, log_cache_mib=i)
    assert rows(store, "SELECT count(*) FROM graph_tokenizer_definitions") == [(1,)]
    assert revisions(store) == list(range(revision - settings_history.RECENT, revision + 1))
    with pytest.raises(KeyError):
        store.settings(1)
    assert store.settings_or_current(1).revision == revision


def test_snapshots_used_by_trajectories_and_jobs_are_kept(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite", configured())
    trajectory = store.create(source())
    jobs = JobService(store, recover=False)
    revision = save(store, 1, log_cache_mib=5)
    jobs.create_job("manual", {"options": {"settings_revision": revision}})
    pinned = revision
    for i in range(settings_history.RECENT + 5):
        revision = save(store, revision, log_cache_mib=i)
    assert {1, pinned} <= set(revisions(store))
    assert store.settings(trajectory.settings_revision).revision == 1
    assert 3 not in revisions(store)


def test_removed_tokenizer_is_deleted_once_no_snapshot_uses_it(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite", configured())
    revision = save(store, 1, tokenizer_profiles={})
    assert rows(store, "SELECT count(*) FROM graph_tokenizer_definitions") == [(1,)]
    for i in range(settings_history.RECENT + 1):
        revision = save(store, revision, log_cache_mib=i)
    assert rows(store, "SELECT count(*) FROM graph_tokenizer_definitions") == [(0,)]


def test_stale_saves_merge_inside_the_window_and_conflict_after_it(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite", configured())
    loaded = store.settings()
    edit = loaded.settings.model_dump(mode="json") | {"tool_schema_extra_chance": 0.25}
    revision = save(store, 1, log_cache_mib=7)
    merged = store.update_settings(edit, loaded.revision, rebase=True).settings
    assert (merged.log_cache_mib, merged.tool_schema_extra_chance) == (7, 0.25)
    revision += 1
    for i in range(settings_history.RECENT + 1):
        revision = save(store, revision, log_cache_mib=i)
    with pytest.raises(RevisionConflict):
        store.update_settings(edit, loaded.revision, rebase=True)


def test_frozen_job_settings_reference_tokenizers_by_hash(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite", configured())
    settings = store.settings().settings
    frozen = store.frozen(settings)
    assert "definition" not in frozen["tokenizer_profiles"]["bytes"]
    assert store.thaw(frozen) == settings
    assert store.thaw(settings.model_dump(mode="json")) == settings


def test_current_snapshot_cannot_be_deleted(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store.connect() as db:
            db.execute("DELETE FROM graph_settings_revisions")


# The layout before settings tables: every revision a full, undeletable JSON copy.
OLD_SCHEMA = """
CREATE TABLE graph_project (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format TEXT NOT NULL, id TEXT NOT NULL UNIQUE, settings_revision INTEGER NOT NULL);
CREATE TABLE graph_settings_revisions (
    revision INTEGER PRIMARY KEY, commit_id TEXT NOT NULL UNIQUE,
    settings TEXT NOT NULL CHECK(json_valid(settings)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE TABLE graph_trajectory_revisions (
    trajectory_id TEXT NOT NULL, revision INTEGER NOT NULL,
    commit_id TEXT NOT NULL UNIQUE, parent_commit_id TEXT,
    source TEXT NOT NULL CHECK(json_valid(source)), intrinsic_hash TEXT NOT NULL,
    intrinsic_revision INTEGER NOT NULL, metadata_revision INTEGER NOT NULL,
    settings_revision INTEGER NOT NULL REFERENCES graph_settings_revisions(revision),
    provenance TEXT NOT NULL CHECK(json_valid(provenance)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(trajectory_id,revision),
    CHECK(json_extract(source,'$.id') = trajectory_id),
    CHECK(json_extract(source,'$.format') = 'kidra-trajectory-graph/1'));
CREATE TABLE graph_trajectories (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
    FOREIGN KEY(id,revision) REFERENCES graph_trajectory_revisions(trajectory_id,revision));
CREATE TABLE graph_tool_usage (
    tool_id TEXT NOT NULL, trajectory_id TEXT NOT NULL REFERENCES graph_trajectories(id),
    source_revision INTEGER NOT NULL, PRIMARY KEY(tool_id,trajectory_id));
CREATE TABLE graph_project_changes (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    entity_id TEXT NOT NULL, revision INTEGER NOT NULL, commit_id TEXT NOT NULL);
CREATE TRIGGER immutable_graph_source_update BEFORE UPDATE ON graph_trajectory_revisions
BEGIN SELECT RAISE(ABORT,'Source revisions are immutable'); END;
CREATE TRIGGER immutable_graph_source_delete BEFORE DELETE ON graph_trajectory_revisions
BEGIN SELECT RAISE(ABORT,'Source revisions are immutable'); END;
CREATE TRIGGER immutable_graph_settings_update BEFORE UPDATE ON graph_settings_revisions
BEGIN SELECT RAISE(ABORT,'Settings revisions are immutable'); END;
CREATE TRIGGER immutable_graph_settings_delete BEFORE DELETE ON graph_settings_revisions
BEGIN SELECT RAISE(ABORT,'Settings revisions are immutable'); END;
"""


def old_project(path, count):
    from trajectory_remix.graph.models import ProjectSettings

    settings = ProjectSettings.model_validate(configured()).model_dump(mode="json")
    trajectory = source()
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA)
        for revision in range(1, count + 1):
            data = settings | {"log_cache_mib": revision}
            db.execute(
                "INSERT INTO graph_settings_revisions(revision,commit_id,settings) VALUES(?,?,?)",
                (revision, str(uuid.uuid4()), canonical(data)),
            )
        db.execute(
            "INSERT INTO graph_project VALUES(1,'kidra-graph-project/1',?,?)",
            (str(uuid.uuid4()), count),
        )
        db.execute(
            """INSERT INTO graph_trajectory_revisions(trajectory_id,revision,commit_id,source,
            intrinsic_hash,intrinsic_revision,metadata_revision,settings_revision,provenance)
            VALUES(?,1,?,?,'h',1,1,2,'{}')""",
            (trajectory["id"], str(uuid.uuid4()), canonical(trajectory)),
        )
        db.execute("INSERT INTO graph_trajectories VALUES(?,1)", (trajectory["id"],))
    return settings | {"log_cache_mib": count}


def test_old_projects_are_upgraded_in_place(tmp_path):
    path, count = tmp_path / "old.sqlite", settings_history.RECENT + 10
    current = old_project(path, count)
    store = GraphStore(path)
    assert store.settings().settings.model_dump(mode="json") == current
    assert store.settings().revision == count
    assert revisions(store) == [2, *range(count - settings_history.RECENT, count + 1)]
    assert store.settings(2).settings.log_cache_mib == 2
    assert rows(store, "SELECT count(*) FROM graph_tokenizer_definitions") == [(1,)]
    assert rows(store, "PRAGMA foreign_key_check") == []
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store.connect() as db:
            db.execute("DELETE FROM graph_trajectory_revisions")
    assert save(GraphStore(path), count, log_cache_mib=1) == count + 1


def test_settings_api_keeps_keys_that_start_with_sa(tmp_path):
    from fastapi.testclient import TestClient

    from trajectory_remix.graph.studio_api import create_studio_app

    data = configured()
    vocab = data["tokenizer_profiles"]["bytes"]["definition"].setdefault("model", {})
    vocab.setdefault("vocab", {})["_save"] = 0
    GraphStore.initialize(tmp_path / "p.sqlite", data)
    app = create_studio_app(tmp_path / "p.sqlite", artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        loaded = client.get("/api/v1/settings").json()
        saved = client.put(
            "/api/v1/settings",
            json={"settings": loaded["settings"], "expected_revision": loaded["revision"]},
        ).json()
    for body in (loaded, saved):
        model = body["settings"]["tokenizer_profiles"]["bytes"]["definition"]["model"]
        assert "_save" in model["vocab"]
