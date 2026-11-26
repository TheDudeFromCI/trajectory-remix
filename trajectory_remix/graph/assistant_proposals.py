"""The Assistant's proposed changes: a copy-on-write working copy per trajectory.

An Assistant session never edits the trajectory itself. Its tools edit a working copy, and
when the session ends the copy is kept here as a proposal, next to the source revision it
started from (the base). A person inspects the changes and applies them, which merges them
onto the trajectory as one edit, or discards them. Requesting changes sends the comments back
and the next session continues from the proposal, so one proposal collects every round.

Like comments and the kept conversation, proposals live outside the source: they never
change a revision, review identity or export until applied.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from . import source_delta

SCHEMA = """CREATE TABLE IF NOT EXISTS graph_assistant_proposals (
    trajectory_id TEXT PRIMARY KEY,
    base_revision INTEGER NOT NULL,
    base TEXT NOT NULL,
    source TEXT NOT NULL,
    comment_ids TEXT NOT NULL,
    updated TEXT NOT NULL)"""


def load(store, trajectory_id):
    """The pending proposal, or None."""
    with store.connect() as db:
        db.execute(SCHEMA)
        row = db.execute(
            "SELECT base_revision,base,source,comment_ids,updated FROM graph_assistant_proposals"
            " WHERE trajectory_id=?",
            (trajectory_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(
        base_revision=row[0],
        base=json.loads(row[1]),
        source=json.loads(row[2]),
        comment_ids=json.loads(row[3]),
        updated=row[4],
    )


def save(store, trajectory_id, *, base_revision, base, source, comment_ids):
    with store.connect() as db:
        db.execute(SCHEMA)
        db.execute(
            "INSERT OR REPLACE INTO graph_assistant_proposals VALUES(?,?,?,?,?,?)",
            (
                trajectory_id,
                base_revision,
                json.dumps(base, ensure_ascii=False),
                json.dumps(source, ensure_ascii=False),
                json.dumps(sorted(set(comment_ids))),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )


def forget(store, trajectory_id):
    with store.connect() as db:
        db.execute(SCHEMA)
        db.execute("DELETE FROM graph_assistant_proposals WHERE trajectory_id=?", (trajectory_id,))


def rebase(store, record):
    """Bring a pending proposal up to the trajectory's current revision before a new session,
    so the agent sees edits made since. Raises ProposalConflict when both changed one entity."""
    proposal = load(store, record.source.id)
    if proposal is None:
        return
    current = record.source.to_dict()
    merged = source_delta.merge(proposal["base"], current, proposal["source"])
    save(
        store,
        record.source.id,
        base_revision=record.revision,
        base=current,
        source=merged,
        comment_ids=proposal["comment_ids"],
    )


def working(store, trajectory_id, live):
    """The source a new session edits: the pending proposal's, else the live one."""
    proposal = load(store, trajectory_id)
    return proposal["source"] if proposal else live.to_dict()


def keep(store, trajectory_id, base_revision, source, addressed):
    """End of a session: keep its working copy as the proposal, or drop the proposal when
    the copy no longer differs from its base."""
    base = store.read(trajectory_id, base_revision).source.to_dict()
    if not source_delta.changed_keys(base, source):
        forget(store, trajectory_id)
        return False
    prior = load(store, trajectory_id)
    save(
        store,
        trajectory_id,
        base_revision=base_revision,
        base=base,
        source=source,
        comment_ids=[*(prior["comment_ids"] if prior else []), *addressed],
    )
    return True


def summary(store, trajectory_id):
    """What the dashboard shows: the changes, and which also changed on the trajectory."""
    proposal = load(store, trajectory_id)
    if proposal is None:
        return None
    current = store.read(trajectory_id).source.to_dict()
    clash = source_delta.conflicts(proposal["base"], current, proposal["source"])
    return dict(
        base_revision=proposal["base_revision"],
        updated=proposal["updated"],
        comment_ids=proposal["comment_ids"],
        changes=source_delta.changes(proposal["base"], proposal["source"]),
        conflicts=[dict(kind=kind, id=ident) for kind, ident in clash],
    )
