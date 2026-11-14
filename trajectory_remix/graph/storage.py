"""Separate new-format SQLite/WAL store with immutable snapshots and source CAS."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

from .models import GraphSource, ProjectSettings, canonical

PROJECT_FORMAT = "kidra-graph-project/1"
PROVENANCE = TypeAdapter(dict[str, JsonValue])


class RevisionConflict(ValueError):
    code = "revision_conflict"

    def __init__(self, entity_id: str, expected_revision: int, current_revision: int, commit_id: str):
        super().__init__(f"Revision conflict for {entity_id}: expected {expected_revision}, current {current_revision}")
        self.entity_id = entity_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        self.commit_id = commit_id  # Bounded reference for explicit reconciliation.


@dataclass(frozen=True)
class SourceRecord:
    source: GraphSource
    revision: int
    commit_id: str
    intrinsic_hash: str
    intrinsic_revision: int
    metadata_revision: int
    settings_revision: int


@dataclass(frozen=True)
class SettingsRecord:
    settings: ProjectSettings
    revision: int
    commit_id: str


SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE graph_project (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format TEXT NOT NULL, id TEXT NOT NULL UNIQUE, settings_revision INTEGER NOT NULL
);
CREATE TABLE graph_settings_revisions (
    revision INTEGER PRIMARY KEY, commit_id TEXT NOT NULL UNIQUE,
    settings TEXT NOT NULL CHECK(json_valid(settings)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
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
    CHECK(json_extract(source,'$.format') = 'kidra-trajectory-graph/1')
);
CREATE TABLE graph_trajectories (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
    FOREIGN KEY(id,revision) REFERENCES graph_trajectory_revisions(trajectory_id,revision)
);
CREATE TABLE graph_tool_usage (
    tool_id TEXT NOT NULL, trajectory_id TEXT NOT NULL REFERENCES graph_trajectories(id),
    source_revision INTEGER NOT NULL,
    PRIMARY KEY(tool_id,trajectory_id)
);
CREATE TABLE graph_project_changes (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    entity_id TEXT NOT NULL, revision INTEGER NOT NULL, commit_id TEXT NOT NULL
);
CREATE TRIGGER immutable_graph_source_update BEFORE UPDATE ON graph_trajectory_revisions
BEGIN SELECT RAISE(ABORT,'Source revisions are immutable'); END;
CREATE TRIGGER immutable_graph_source_delete BEFORE DELETE ON graph_trajectory_revisions
BEGIN SELECT RAISE(ABORT,'Source revisions are immutable'); END;
CREATE TRIGGER immutable_graph_settings_update BEFORE UPDATE ON graph_settings_revisions
BEGIN SELECT RAISE(ABORT,'Settings revisions are immutable'); END;
CREATE TRIGGER immutable_graph_settings_delete BEFORE DELETE ON graph_settings_revisions
BEGIN SELECT RAISE(ABORT,'Settings revisions are immutable'); END;
CREATE TRIGGER immutable_graph_identity BEFORE UPDATE OF id ON graph_trajectories
BEGIN SELECT RAISE(ABORT,'Trajectory identity is immutable'); END;
CREATE TRIGGER immutable_graph_project BEFORE UPDATE OF id,format ON graph_project
BEGIN SELECT RAISE(ABORT,'Project identity is immutable'); END;
"""


class GraphStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError(f"Graph project does not exist: {self.path}")
        try:
            with self.connect() as db:
                row = db.execute("SELECT format,id FROM graph_project WHERE singleton=1").fetchone()
        except sqlite3.DatabaseError as error:
            raise ValueError("Not a graph project; no legacy reader or migration is available") from error
        if row is None or row[0] != PROJECT_FORMAT:
            raise ValueError("Unsupported graph project format")
        self.project_id = row[1]

    @classmethod
    def initialize(cls, path: Path, settings: ProjectSettings | dict | None = None):
        settings = ProjectSettings.model_validate({} if settings is None else settings)
        encoded = canonical(settings.model_dump(mode="json"))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA journal_mode=WAL")
                with db:
                    db.executescript(SCHEMA)
                    commit = str(uuid4())
                    db.execute("INSERT INTO graph_settings_revisions(revision,commit_id,settings) VALUES(1,?,?)",
                               (commit, encoded))
                    db.execute("INSERT INTO graph_project VALUES(1,?,?,1)", (PROJECT_FORMAT, str(uuid4())))
            return cls(path)
        except BaseException:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            raise

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db

    @staticmethod
    def _record(row):
        if row is None:
            raise KeyError("Trajectory/revision not found")
        return SourceRecord(GraphSource.model_validate_json(row["source"]), row["revision"], row["commit_id"],
                            row["intrinsic_hash"], row["intrinsic_revision"], row["metadata_revision"],
                            row["settings_revision"])

    def _read(self, db, trajectory_id, revision=None):
        if revision is None:
            row = db.execute("""SELECT r.* FROM graph_trajectories t JOIN graph_trajectory_revisions r
                ON r.trajectory_id=t.id AND r.revision=t.revision WHERE t.id=?""", (trajectory_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM graph_trajectory_revisions WHERE trajectory_id=? AND revision=?",
                             (trajectory_id, revision)).fetchone()
        return self._record(row)

    def read(self, trajectory_id: str, revision: int | None = None):
        with self.connect() as db:
            return self._read(db, trajectory_id, revision)

    def _settings(self, db, revision=None):
        if revision is None:
            revision = db.execute("SELECT settings_revision FROM graph_project").fetchone()[0]
        row = db.execute("SELECT * FROM graph_settings_revisions WHERE revision=?", (revision,)).fetchone()
        if row is None:
            raise KeyError("Settings revision not found")
        return SettingsRecord(ProjectSettings.model_validate_json(row["settings"]), revision, row["commit_id"])

    def settings(self, revision: int | None = None):
        with self.connect() as db:
            db.execute("BEGIN")
            return self._settings(db, revision)

    def update_settings(self, settings: ProjectSettings | dict, expected_revision: int):
        settings = ProjectSettings.model_validate(settings)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._settings(db)
            self._check_revision("settings", expected_revision, old)
            revision, commit = old.revision + 1, str(uuid4())
            db.execute("INSERT INTO graph_settings_revisions(revision,commit_id,settings) VALUES(?,?,?)",
                       (revision, commit, canonical(settings.model_dump(mode="json"))))
            db.execute("UPDATE graph_project SET settings_revision=?", (revision,))
            self._change(db, "settings", self.project_id, revision, commit)
            return SettingsRecord(settings.model_copy(deep=True), revision, commit)

    @staticmethod
    def _check_revision(entity_id, expected, current):
        if type(expected) is not int or expected < 1:
            raise ValueError("Expected revision must be a positive integer")
        if expected != current.revision:
            raise RevisionConflict(entity_id, expected, current.revision, current.commit_id)

    @staticmethod
    def _change(db, kind, entity, revision, commit):
        db.execute("INSERT INTO graph_project_changes(kind,entity_id,revision,commit_id) VALUES(?,?,?,?)",
                   (kind, entity, revision, commit))
        db.execute("DELETE FROM graph_project_changes WHERE cursor <= (SELECT max(cursor)-1000 FROM graph_project_changes)")

    def create(self, source: GraphSource | dict, provenance: dict | None = None):
        source = GraphSource.model_validate(source)
        provenance = PROVENANCE.validate_python({} if provenance is None else provenance, strict=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM graph_trajectories WHERE id=?", (source.id,)).fetchone():
                raise ValueError("Trajectory already exists")
            return self._commit(db, source, None, provenance)

    def replace(self, trajectory_id: str, source: GraphSource | dict, expected_revision: int,
                provenance: dict | None = None):
        source = GraphSource.model_validate(source)
        if source.id != trajectory_id:
            raise ValueError("Trajectory identity is immutable")
        provenance = PROVENANCE.validate_python({} if provenance is None else provenance, strict=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._read(db, trajectory_id)
            self._check_revision(trajectory_id, expected_revision, old)
            return self._commit(db, source, old, provenance)

    def _commit(self, db, source, old, provenance):
        revision, commit = (old.revision + 1 if old else 1), str(uuid4())
        source_hash = source.intrinsic_hash
        intrinsic_revision = old.intrinsic_revision + (source_hash != old.intrinsic_hash) if old else 1
        old_metadata = old.source.to_dict().get("metadata") if old else None
        metadata_revision = old.metadata_revision + (old_metadata != source.to_dict().get("metadata")) if old else 1
        settings_revision = db.execute("SELECT settings_revision FROM graph_project").fetchone()[0]
        db.execute("""INSERT INTO graph_trajectory_revisions(trajectory_id,revision,commit_id,parent_commit_id,
            source,intrinsic_hash,intrinsic_revision,metadata_revision,settings_revision,provenance)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (source.id, revision, commit, old.commit_id if old else None,
            canonical(source.to_dict()), source_hash, intrinsic_revision, metadata_revision,
            settings_revision, canonical(provenance)))
        db.execute("INSERT INTO graph_trajectories(id,revision) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision",
                   (source.id, revision))
        db.execute("DELETE FROM graph_tool_usage WHERE trajectory_id=?", (source.id,))
        tools = sorted({e.tool for e in source.events.values() if e.kind == "tool_call"})
        db.executemany("INSERT INTO graph_tool_usage VALUES(?,?,?)", [(t, source.id, revision) for t in tools])
        self._change(db, "trajectory", source.id, revision, commit)
        return self._read(db, source.id)

    def using_tool(self, tool_id: str, *, limit: int = 100, offset: int = 0):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid pagination")
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT trajectory_id,source_revision FROM graph_tool_usage
                WHERE tool_id=? ORDER BY trajectory_id LIMIT ? OFFSET ?""", (tool_id, limit, offset))]
