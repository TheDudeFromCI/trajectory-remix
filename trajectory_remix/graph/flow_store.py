"""The project's Flow, stored inside the project database so a project is one file.

One row holds the YAML text exactly as saved (comments included). With no row the
packaged default applies. A project that still has a legacy flow.yaml beside its
database has that file imported once, the first time the flow is read; after that the
database copy is the only one used and the file is left untouched.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

PACKAGED = "packaged"
SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_flow (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    updated TEXT NOT NULL);
"""


class FlowConflict(ValueError):
    code = "flow_conflict"

    def __init__(self, expected, current):
        super().__init__("The project flow changed since it was loaded; reload before saving")
        self.expected_revision, self.current_revision = expected, current


def legacy_path(store):
    return Path(store.path).parent / "flow.yaml"


def revision(text):
    return PACKAGED if text is None else hashlib.sha256(text.encode()).hexdigest()


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _begin(db):
    db.executescript(SCHEMA)  # Commits implicitly, so it runs before the transaction.
    db.execute("BEGIN IMMEDIATE")


def _row(db, store):
    row = db.execute("SELECT text, origin FROM graph_flow WHERE id = 1").fetchone()
    if row is None and legacy_path(store).is_file():
        text = legacy_path(store).read_text()
        db.execute("INSERT INTO graph_flow VALUES(1, ?, 'imported flow.yaml', ?)", (text, _now()))
        row = (text, "imported flow.yaml")
    return row


def project_text(store):
    """The saved flow text, or None when the project uses the packaged default."""
    with store.connect() as db:  # Commits on exit.
        _begin(db)
        row = _row(db, store)
    return None if row is None else row[0]


def write(store, text, expected_revision):
    """Replace the flow text if it is still at expected_revision."""
    with store.connect() as db:  # Commits on exit; an exception rolls back.
        _begin(db)
        row = _row(db, store)
        current = revision(None if row is None else row[0])
        if current != expected_revision:
            raise FlowConflict(expected_revision, current)
        db.execute(
            "INSERT INTO graph_flow VALUES(1, ?, 'editor', ?) "
            "ON CONFLICT(id) DO UPDATE SET text=excluded.text, origin=excluded.origin, "
            "updated=excluded.updated",
            (text, _now()),
        )
