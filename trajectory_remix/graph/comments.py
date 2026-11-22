"""Reviewer comments on a trajectory, its events or its windows.

Comments are collaboration notes for whoever fixes the trajectory next, a person or an
agent: "this tool call closes the window too early". They live in their own table, not
in the source, so adding or resolving one never changes a source revision, a review
identity or an export, and works while a job holds the trajectory.

A comment is open (work to do), addressed (an agent says it's fixed and explained how in
the thread) or resolved. Only a person resolves: they accept an addressed comment, or
request changes, which reopens it with their reply. An agent can never mark its own work
complete.
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
    status TEXT NOT NULL CHECK(status IN ('open','addressed','resolved')),
    created TEXT NOT NULL,
    updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS graph_comments_trajectory ON graph_comments(trajectory_id, status);
CREATE TABLE IF NOT EXISTS graph_comment_replies (
    id TEXT PRIMARY KEY,
    comment_id TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL CHECK(author IN ('user','agent')),
    created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS graph_comment_replies_comment ON graph_comment_replies(comment_id);
"""
COLUMNS = (
    "id,trajectory_id,target_kind,target_id,body,author,source_revision,status,created,updated"
)
Status = Literal["open", "addressed", "resolved"]
# Filters: a status, or "unresolved" for open and addressed together.
StatusFilter = Literal["open", "addressed", "resolved", "unresolved"]
MAX_BODY = 10_000


class Target(DTO):
    kind: Literal["trajectory", "event", "window"]
    id: str | None = Field(default=None, min_length=1, max_length=256)


class NewComment(DTO):
    target: Target
    body: str = Field(min_length=1, max_length=MAX_BODY)


class NewReply(DTO):
    body: str = Field(min_length=1, max_length=MAX_BODY)
    # A person's "Request changes": reply and reopen in one step.
    reopen: bool = False


class CommentEdit(DTO):
    """A person's edit. Agents use address() instead; they can never resolve."""

    status: Literal["open", "resolved"] | None = None
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY)


def status_clause(status: StatusFilter | None, column="status"):
    """SQL for a status filter, or "1" for none."""
    if status is None:
        return "1", []
    if status == "unresolved":
        return f"{column} IN ('open','addressed')", []
    return f"{column}=?", [status]


def _upgrade(db):
    """Rebuild a table made before the addressed state: its resolution note becomes the
    last agent reply, and the status check gains 'addressed'."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='graph_comments'").fetchone()
    if row is None or "addressed" in row[0]:
        return
    db.execute("ALTER TABLE graph_comments RENAME TO graph_comments_old")
    db.execute("DROP INDEX IF EXISTS graph_comments_trajectory")
    db.executescript(SCHEMA)
    db.execute(f"INSERT INTO graph_comments({COLUMNS}) SELECT {COLUMNS} FROM graph_comments_old")
    db.execute(
        "INSERT INTO graph_comment_replies SELECT lower(hex(randomblob(16))),id,resolution,"
        "'agent',updated FROM graph_comments_old WHERE resolution IS NOT NULL"
    )
    db.execute("DROP TABLE graph_comments_old")


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
            _upgrade(db)
            db.executescript(SCHEMA)

    def _row(self, db, ident):
        row = db.execute(f"SELECT {COLUMNS} FROM graph_comments WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return _with_replies(db, [row])[0]

    def add(self, trajectory_id, comment: NewComment, author: Literal["user", "agent"]):
        record = self.store.read(trajectory_id)  # Missing trajectories raise KeyError.
        target_id = _target(record.source, comment.target)
        now, ident = _now(), str(uuid4())
        with self.store.connect() as db:
            db.execute(
                f"INSERT INTO graph_comments({COLUMNS}) VALUES(?,?,?,?,?,?,?,'open',?,?)",
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

    def address(self, ident, note):
        """An agent says it's done: its note joins the thread and a person reviews it."""
        return self.reply(ident, NewReply(body=note), "agent", status="addressed")

    def delete(self, ident):
        with self.store.connect() as db:
            self._row(db, ident)
            db.execute("DELETE FROM graph_comment_replies WHERE comment_id=?", (ident,))
            db.execute("DELETE FROM graph_comments WHERE id=?", (ident,))
        return dict(id=ident, deleted=True)

    def reply(self, ident, reply: NewReply, author: Literal["user", "agent"], status=None):
        """Add a reply to a comment's thread. It keeps its status unless a person asks to
        reopen it, or status is given (address)."""
        if reply.reopen:
            if author != "user":
                raise ValueError("Only a person can request changes")
            status = "open"
        now = _now()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._row(db, ident)
            db.execute(
                "INSERT INTO graph_comment_replies VALUES(?,?,?,?,?)",
                (str(uuid4()), ident, reply.body, author, now),
            )
            db.execute(
                "UPDATE graph_comments SET updated=?,status=? WHERE id=?",
                (now, status or current["status"], ident),
            )
            return self._row(db, ident)

    def delete_reply(self, ident, reply_id):
        with self.store.connect() as db:
            gone = db.execute(
                "DELETE FROM graph_comment_replies WHERE id=? AND comment_id=?", (reply_id, ident)
            ).rowcount
            if not gone:
                raise KeyError(reply_id)
            return self._row(db, ident)

    def for_trajectory(self, trajectory_id, status: StatusFilter | None = None):
        """Comments on one trajectory: awaiting review, open, then resolved. Each is marked
        if its target is gone."""
        source = self.store.read(trajectory_id).source
        clause, extra = status_clause(status)
        with self.store.connect() as db:
            rows = db.execute(
                f"SELECT {COLUMNS} FROM graph_comments WHERE trajectory_id=? AND {clause} "
                "ORDER BY CASE status WHEN 'addressed' THEN 0 WHEN 'open' THEN 1 ELSE 2 END, rowid",
                [trajectory_id, *extra],
            ).fetchall()
            items = _with_replies(db, rows)
        return [c | dict(target_exists=_exists(source, c)) for c in items]

    def listing(self, status: StatusFilter | None = "open", *, limit=20, offset=0):
        """Comments across the project, oldest first; deleted trajectories are left out."""
        clause, args = status_clause(status, "c.status")
        where = f"AND {clause}"
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
            items = _with_replies(db, rows)
        more = offset + len(items) < total
        return dict(items=items, total=total, next_offset=offset + len(items) if more else None)

    def counts(self):
        """Unresolved comments per trajectory, split into open and addressed."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT trajectory_id, sum(status='open'), sum(status='addressed') "
                "FROM graph_comments WHERE status IN ('open','addressed') GROUP BY trajectory_id"
            ).fetchall()
        return {r[0]: dict(open=r[1], addressed=r[2]) for r in rows}


def _with_replies(db, rows):
    """Comment rows as dicts, each with its replies, oldest first."""
    items = [dict(r) | dict(replies=[]) for r in rows]
    by_id = {c["id"]: c for c in items}
    if by_id:
        marks = ",".join("?" * len(by_id))
        for r in db.execute(
            "SELECT id,comment_id,body,author,created FROM graph_comment_replies "
            f"WHERE comment_id IN ({marks}) ORDER BY rowid",
            list(by_id),
        ):
            by_id[r["comment_id"]]["replies"].append(dict(r))
    return items


def _exists(source, row):
    kind, ident = row["target_kind"], row["target_id"]
    return kind == "trajectory" or ident in getattr(source, f"{kind}s")
