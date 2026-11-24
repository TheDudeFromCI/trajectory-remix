"""The last finished Assistant conversation per trajectory.

The next "Send open comments to agent" continues it, appending the new comments as a user
message. Like comments, it lives outside the source, so it never changes a revision,
review identity or export.
"""

import json
from datetime import UTC, datetime

SCHEMA = """CREATE TABLE IF NOT EXISTS graph_assistant_conversations (
    trajectory_id TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    updated TEXT NOT NULL)"""


def load(store, trajectory_id):
    """The kept conversation's messages, or None."""
    with store.connect() as db:
        db.execute(SCHEMA)
        row = db.execute(
            "SELECT messages FROM graph_assistant_conversations WHERE trajectory_id=?",
            (trajectory_id,),
        ).fetchone()
    return json.loads(row[0]) if row else None


def save(store, trajectory_id, messages):
    with store.connect() as db:
        db.execute(SCHEMA)
        db.execute(
            "INSERT OR REPLACE INTO graph_assistant_conversations VALUES(?,?,?)",
            (
                trajectory_id,
                json.dumps(messages, ensure_ascii=False),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )


def forget(store, trajectory_id):
    with store.connect() as db:
        db.execute(SCHEMA)
        db.execute(
            "DELETE FROM graph_assistant_conversations WHERE trajectory_id=?", (trajectory_id,)
        )
