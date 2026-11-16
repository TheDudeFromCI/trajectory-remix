"""Removing finished jobs: the job record, its logs and its leased artifact."""

from __future__ import annotations

from .jobs import TERMINAL, OwnershipConflict

# Child tables keyed directly by job_id, deleted before the job row itself.
_BY_JOB = (
    "graph_job_logs",
    "graph_log_cursors",
    "graph_checkpoints",
    "graph_steps",
    "graph_requests",
    "graph_bulk_items",
)


def clear_job(jobs, artifacts, job):
    """Delete a completed, cancelled or failed job. Running/paused jobs are refused."""
    with jobs.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = jobs._job(db, job)
        if row["state"] not in TERMINAL:
            raise OwnershipConflict("Cancel the job before clearing it")
        if db.execute("SELECT 1 FROM graph_owners WHERE job_id=?", (job,)).fetchone():
            raise OwnershipConflict("Job still owns trajectories")
        for table in _BY_JOB:
            db.execute(f"DELETE FROM {table} WHERE job_id=?", (job,))  # noqa: S608
        db.execute(
            "DELETE FROM graph_attempts WHERE run_id IN (SELECT id FROM graph_runs WHERE job_id=?)",
            (job,),
        )
        db.execute("DELETE FROM graph_runs WHERE job_id=?", (job,))
        db.execute("DELETE FROM graph_jobs WHERE id=?", (job,))
        artifact = row["artifact"]
    if artifact:
        artifacts.discard(artifact)
    return dict(cleared=True, job_id=job)
