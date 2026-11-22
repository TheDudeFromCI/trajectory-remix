"""Reviewer comments on a trajectory, its events or its windows.

Comments are collaboration notes for whoever fixes the trajectory next, a person or an
agent: "this tool call closes the window too early". They live in their own table, not
in the source, so adding or resolving one never changes a source revision, a review
identity or an export, and works while a job holds the trajectory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .models import DTO

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_comments (
    id TEXT PRIMARY KEY,
    trajectory_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('trajectory','event','window')),
    target_id TEXT,
    body TEXT NOT NULL,
    author TEXT NOT NULL CHECK(author IN ('user','agent')),
    source_revision INTEGER,
    status TEXT NOT NULL CHECK(status IN ('open','resolved')),
    resolution TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS graph_comments_trajectory ON graph_comments(trajectory_id, status);
"""
COLUMNS = (
    "id,trajectory_id,target_kind,target_id,body,author,source_revision,status,resolution,"
    "created,updated"
)
Status = Literal["open", "resolved"]
MAX_BODY = 10_000


class Target(DTO):
    kind: Literal["trajectory", "event", "window"]
    id: str | None = Field(default=None, min_length=1, max_length=256)


class NewComment(DTO):
    target: Target
    body: str = Field(min_length=1, max_length=MAX_BODY)


class CommentEdit(DTO):
    status: Status | None = None
    resolution: str | None = Field(default=None, max_length=MAX_BODY)
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY)


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _target(source, target):
    """Check the target names something in the current source; trajectory has no ID."""
    if target.kind == "trajectory":
        if target.id is not None:
            raise ValueError("A trajectory comment takes no target ID")
        return None
    if target.id is None:
        raise ValueError(f"Name the {target.kind} this comment is about")
    if target.id not in getattr(source, f"{target.kind}s"):
        raise ValueError(f"This trajectory has no {target.kind} '{target.id}'")
    return target.id


class CommentService:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)

    def _row(self, db, ident):
        row = db.execute(f"SELECT {COLUMNS} FROM graph_comments WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return dict(row)

    def add(self, trajectory_id, comment: NewComment, author: Literal["user", "agent"]):
        record = self.store.read(trajectory_id)  # Missing trajectories raise KeyError.
        target_id = _target(record.source, comment.target)
        now, ident = _now(), str(uuid4())
        with self.store.connect() as db:
            db.execute(
                f"INSERT INTO graph_comments({COLUMNS}) VALUES(?,?,?,?,?,?,?,'open',NULL,?,?)",
                (
                    ident,
                    trajectory_id,
                    comment.target.kind,
                    target_id,
                    comment.body,
                    author,
                    record.revision,
                    now,
                    now,
                ),
            )
            return self._row(db, ident)

    def edit(self, ident, change: CommentEdit):
        fields = change.model_dump(exclude_unset=True)
        if fields.get("status") == "open":
            fields.setdefault("resolution", None)  # Reopening clears the old answer.
        if not fields:
            raise ValueError("Nothing to change")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, ident)
            assignments = ",".join(f"{key}=?" for key in fields)
            db.execute(
                f"UPDATE graph_comments SET {assignments},updated=? WHERE id=?",
                (*fields.values(), _now(), ident),
            )
            return self._row(db, ident)

    def resolve(self, ident, resolution):
        return self.edit(ident, CommentEdit(status="resolved", resolution=resolution))

    def delete(self, ident):
        with self.store.connect() as db:
            self._row(db, ident)
            db.execute("DELETE FROM graph_comments WHERE id=?", (ident,))
        return dict(id=ident, deleted=True)

    def for_trajectory(self, trajectory_id, status: Status | None = None):
        """All comments on one trajectory, open first, each marked if its target is gone."""
        source = self.store.read(trajectory_id).source
        where, args = "trajectory_id=?", [trajectory_id]
        if status:
            where, args = where + " AND status=?", [*args, status]
        with self.store.connect() as db:
            rows = db.execute(
                f"SELECT {COLUMNS} FROM graph_comments WHERE {where} "
                "ORDER BY status='resolved', rowid",
                args,
            ).fetchall()
        return [dict(r) | dict(target_exists=_exists(source, r)) for r in rows]

    def listing(self, status: Status | None = "open", *, limit=20, offset=0):
        """Comments across the project, oldest first; deleted trajectories are left out."""
        where, args = "", []
        if status:
            where, args = "AND c.status=?", [status]
        with self.store.connect() as db:
            total = db.execute(
                "SELECT count(*) FROM graph_comments c JOIN graph_trajectories t "
                f"ON t.id=c.trajectory_id WHERE 1 {where}",
                args,
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT {', '.join('c.' + c for c in COLUMNS.split(','))} FROM graph_comments c "
                f"JOIN graph_trajectories t ON t.id=c.trajectory_id WHERE 1 {where} "
                "ORDER BY c.rowid LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        items = [dict(r) for r in rows]
        more = offset + len(items) < total
        return dict(items=items, total=total, next_offset=offset + len(items) if more else None)

    def open_counts(self):
        """Open comments per trajectory, for the dataset list."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT trajectory_id, count(*) FROM graph_comments WHERE status='open' "
                "GROUP BY trajectory_id"
            ).fetchall()
        return {r[0]: r[1] for r in rows}


def _exists(source, row):
    kind, ident = row["target_kind"], row["target_id"]
    return kind == "trajectory" or ident in getattr(source, f"{kind}s")
