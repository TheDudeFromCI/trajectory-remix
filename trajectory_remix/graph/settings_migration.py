"""Upgrade projects that keep every settings revision as one full JSON document.

Older projects store each settings save as a complete copy (tokenizer
definitions included) that can never be deleted, and every trajectory revision
holds a foreign key to one. The upgrade, in one transaction:

1. creates the settings tables and fills them from the current revision;
2. rewrites each snapshot in the stored form, keeping one copy per tokenizer;
3. rebuilds ``graph_trajectory_revisions`` without the foreign key, so unused
   snapshots can be pruned, then prunes them (see settings_history.py).

The file is vacuumed afterwards to return the freed space to the disk.
"""

import json
import sqlite3
from contextlib import closing

from . import settings_history, settings_tables
from .models import canonical

_SNAPSHOT_TRIGGERS = (
    "DROP TRIGGER IF EXISTS immutable_graph_settings_update",
    "DROP TRIGGER IF EXISTS immutable_graph_settings_delete",
    """CREATE TRIGGER immutable_graph_settings_update BEFORE UPDATE ON graph_settings_revisions
    BEGIN SELECT RAISE(ABORT,'Settings revisions are immutable'); END""",
    """CREATE TRIGGER immutable_graph_settings_delete BEFORE DELETE ON graph_settings_revisions
    WHEN old.revision = (SELECT settings_revision FROM graph_project)
    BEGIN SELECT RAISE(ABORT,'The current settings revision is immutable'); END""",
)


def needed(db) -> bool:
    sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_tokenizer_profiles'"
    return db.execute(sql).fetchone() is None


def _store_snapshots(db):
    db.execute("DROP TRIGGER IF EXISTS immutable_graph_settings_update")
    rows = db.execute("SELECT revision,settings FROM graph_settings_revisions").fetchall()
    for revision, text in rows:
        stored = settings_tables.stored_form(db, json.loads(text))
        db.execute(
            "UPDATE graph_settings_revisions SET settings=? WHERE revision=?",
            (canonical(stored), revision),
        )
    for statement in _SNAPSHOT_TRIGGERS:
        db.execute(statement)


def _rebuild_trajectory_revisions(db):
    from .storage import REVISION_TRIGGERS, TRAJECTORY_REVISIONS

    db.execute("ALTER TABLE graph_trajectory_revisions RENAME TO graph_trajectory_revisions_old")
    db.execute(TRAJECTORY_REVISIONS)
    db.execute(
        "INSERT INTO graph_trajectory_revisions SELECT * FROM graph_trajectory_revisions_old"
    )
    db.execute("DROP TABLE graph_trajectory_revisions_old")
    for statement in REVISION_TRIGGERS.split("END;")[:-1]:
        if "graph_trajectory_revisions" in statement:
            db.execute(statement + "END")


def _upgrade(db):
    settings_tables.create(db)
    _store_snapshots(db)
    current = db.execute("SELECT settings_revision FROM graph_project").fetchone()[0]
    data, _ = settings_history.load(db, current)
    settings_tables.write(db, data)
    _rebuild_trajectory_revisions(db)
    settings_history.prune(db)
    problems = db.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise sqlite3.IntegrityError(f"Settings upgrade left broken references: {problems[:5]}")


def migrate(path) -> bool:
    """Upgrade the project at ``path`` if needed; return whether it was upgraded."""
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as db:
        if not needed(db):
            return False
        # Rebuilding a referenced table requires foreign keys off (outside the transaction).
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("PRAGMA legacy_alter_table=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            if not needed(db):  # Another process upgraded it first.
                db.execute("ROLLBACK")
                return False
            _upgrade(db)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        try:
            db.execute("VACUUM")
        except sqlite3.OperationalError:
            pass  # Another connection is open; the space is reused by later writes instead.
        return True
