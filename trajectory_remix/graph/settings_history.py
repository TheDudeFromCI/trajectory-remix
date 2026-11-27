"""Settings snapshots that are kept only while something still uses them.

Every save records a snapshot (in the stored form of settings_tables.py, so a
tokenizer definition is never copied). After each save, snapshots are pruned
except for:

* the current revision and the ``RECENT`` revisions before it, so a save made
  from a recently loaded dialog can still be merged onto the latest settings;
* revisions referenced by the latest revision of any trajectory;
* revisions pinned by any job still in the jobs table (jobs freeze the
  settings they started with). Pins live in ``graph_settings_pins`` so pruning
  never has to parse job configurations.

Tokenizer definitions no longer referenced by the current profiles or by a kept
snapshot are deleted with them.
"""

import json

from . import settings_tables
from .models import canonical

RECENT = 20
PINS = """CREATE TABLE IF NOT EXISTS graph_settings_pins (
    job_id TEXT PRIMARY KEY REFERENCES graph_jobs(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL)"""


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


def config_revision(config):
    """The settings revision a job configuration froze, if any."""
    options = config.get("options")
    revision = config.get("settings_revision")
    if revision is None and isinstance(options, dict):
        revision = options.get("settings_revision")
    return revision if type(revision) is int else None


def ensure_pins(db):
    """Create the pin table; the first time, record the revisions of existing jobs."""
    if not _has_table(db, "graph_jobs") or _has_table(db, "graph_settings_pins"):
        return
    db.execute(PINS)
    db.execute(
        """INSERT INTO graph_settings_pins SELECT id, revision FROM (
        SELECT id, coalesce(json_extract(config,'$.settings_revision'),
            json_extract(config,'$.options.settings_revision')) AS revision FROM graph_jobs)
        WHERE typeof(revision)='integer'"""
    )


def pin(db, job_id, config):
    """Pin the settings a new job froze, refusing a snapshot that was already pruned."""
    revision = config_revision(config)
    if revision is None:
        return
    sql = "SELECT 1 FROM graph_settings_revisions WHERE revision=?"
    if db.execute(sql, (revision,)).fetchone() is None:
        raise ValueError("Project settings changed while the job was starting; try again")
    ensure_pins(db)
    db.execute("INSERT INTO graph_settings_pins VALUES(?,?)", (job_id, revision))


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
    if _has_table(db, "graph_settings_pins"):
        keep |= {r for (r,) in db.execute("SELECT DISTINCT revision FROM graph_settings_pins")}
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
