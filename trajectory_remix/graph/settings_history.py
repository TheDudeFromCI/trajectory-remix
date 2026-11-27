"""Settings snapshots that are kept only while something still uses them.

Every save records a snapshot (in the stored form of settings_tables.py, so a
tokenizer definition is never copied). After each save, snapshots are pruned
except for:

* the current revision and the ``RECENT`` revisions before it, so a save made
  from a recently loaded dialog can still be merged onto the latest settings;
* revisions referenced by the latest revision of any trajectory;
* revisions referenced by any job still in the jobs table (jobs freeze the
  settings they started with).

Tokenizer definitions no longer referenced by the current profiles or by a kept
snapshot are deleted with them.
"""

import json

from . import settings_tables
from .models import canonical

RECENT = 20


def record(db, revision, commit, data):
    db.execute(
        "INSERT INTO graph_settings_revisions(revision,commit_id,settings) VALUES(?,?,?)",
        (revision, commit, canonical(settings_tables.stored_form(db, data))),
    )


def load(db, revision):
    """(settings JSON with definitions, commit id) for a kept revision; KeyError otherwise."""
    row = db.execute(
        "SELECT settings,commit_id FROM graph_settings_revisions WHERE revision=?", (revision,)
    ).fetchone()
    if row is None:
        raise KeyError("Settings revision not found")
    return settings_tables.resolved_form(db, json.loads(row[0])), row[1]


def _has_table(db, name):
    sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
    return db.execute(sql, (name,)).fetchone() is not None


def in_use(db):
    """Revisions that must keep their snapshot."""
    current = db.execute("SELECT settings_revision FROM graph_project").fetchone()[0]
    keep = set(range(max(1, current - RECENT), current + 1))
    keep |= {
        r
        for (r,) in db.execute(
            """SELECT DISTINCT r.settings_revision FROM graph_trajectories t
            JOIN graph_trajectory_revisions r ON r.trajectory_id=t.id AND r.revision=t.revision"""
        )
    }
    if _has_table(db, "graph_jobs"):
        for top, nested in db.execute(
            """SELECT json_extract(config,'$.settings_revision'),
            json_extract(config,'$.options.settings_revision') FROM graph_jobs"""
        ):
            keep |= {r for r in (top, nested) if type(r) is int}
    return keep


def prune(db):
    keep = in_use(db)
    stale = [
        r for (r,) in db.execute("SELECT revision FROM graph_settings_revisions") if r not in keep
    ]
    db.executemany("DELETE FROM graph_settings_revisions WHERE revision=?", [(r,) for r in stale])
    db.execute(
        """DELETE FROM graph_tokenizer_definitions WHERE hash NOT IN (
        SELECT definition_hash FROM graph_tokenizer_profiles
        UNION SELECT json_extract(p.value,'$.definition_hash')
        FROM graph_settings_revisions s, json_each(s.settings,'$.tokenizer_profiles') p)"""
    )
    return stale
