"""Optional automatic reconnect for endpoints paused after their retries ran out."""

import sqlite3

COLUMNS = {"reconnect_seconds": "REAL", "resume_at": "REAL"}


def ensure_columns(db):
    """Add the reconnect columns to a graph_endpoints table created before they existed."""
    have = {row[1] for row in db.execute("PRAGMA table_info(graph_endpoints)")}
    for name, kind in COLUMNS.items():
        if name not in have:
            try:
                db.execute(f"ALTER TABLE graph_endpoints ADD COLUMN {name} {kind}")
            except sqlite3.OperationalError:  # Another process added it first.
                pass


def schedule(db, endpoint, now):
    """Record when a just-paused endpoint should reconnect (no-op when disabled)."""
    db.execute(
        "UPDATE graph_endpoints SET resume_at=? WHERE id=? AND reconnect_seconds IS NOT NULL",
        (None, endpoint),
    )
    db.execute(
        "UPDATE graph_endpoints SET resume_at=?+reconnect_seconds "
        "WHERE id=? AND reconnect_seconds IS NOT NULL",
        (now, endpoint),
    )


def resume_due(db, now):
    """Unpause endpoints whose reconnect time has passed; return their ids."""
    due = [
        row[0]
        for row in db.execute(
            "SELECT id FROM graph_endpoints WHERE paused=1 AND resume_at IS NOT NULL AND resume_at<=?",
            (now,),
        )
    ]
    for ident in due:
        db.execute("UPDATE graph_endpoints SET paused=0,resume_at=NULL WHERE id=?", (ident,))
        db.execute(
            "UPDATE graph_requests SET retries=0 WHERE endpoint=? AND state='queued'", (ident,)
        )
    return due
