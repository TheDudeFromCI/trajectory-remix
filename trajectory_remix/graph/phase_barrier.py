"""Phase barrier: every trajectory finishes Draft and Assembly before any review starts.

With generation.phase_barrier on, a run has two phases:

0. Draft and Assembly. A trajectory that reaches review parks: its owner row becomes
   mode 'parked', which does not count toward the active-trajectory cap, so later
   attempts can start. When no attempt is pending and every owned trajectory is parked
   or held, the run moves to phase 1 and the job pauses, so models can be swapped.
1. Reviews. Resume releases the parked trajectories into Fast and Critical Review.

The phase is stored per job, so it survives a server restart.
"""

TABLE = """CREATE TABLE IF NOT EXISTS graph_job_phases (
 job_id TEXT PRIMARY KEY REFERENCES graph_jobs(id), phase INTEGER NOT NULL)"""
GENERATION, REVIEW = 0, 1
REVIEW_PHASES = {"review", "repair"}


def phase(store, job):
    with store.connect() as db:
        db.execute(TABLE)
        row = db.execute("SELECT phase FROM graph_job_phases WHERE job_id=?", (job,)).fetchone()
    return row[0] if row else GENERATION


def advance(store, job):
    with store.connect() as db:
        db.execute(TABLE)
        db.execute(
            "INSERT INTO graph_job_phases VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET phase=?",
            (job, REVIEW, REVIEW),
        )


def set_mode(store, job, trajectory, mode):
    with store.connect() as db:
        db.execute(
            "UPDATE graph_owners SET mode=? WHERE trajectory_id=? AND job_id=?",
            (mode, trajectory, job),
        )


def modes(store, job):
    with store.connect() as db:
        return dict(
            db.execute("SELECT trajectory_id, mode FROM graph_owners WHERE job_id=?", (job,))
        )


def pending_attempts(store, job):
    with store.connect() as db:
        return bool(
            db.execute(
                """SELECT 1 FROM graph_attempts a JOIN graph_jobs j ON a.run_id=j.run_id
                WHERE j.id=? AND a.state='pending' LIMIT 1""",
                (job,),
            ).fetchone()
        )


def waits(cp):
    """True when this checkpoint's next step belongs to the review phase."""
    return bool(cp) and cp.get("phase") in REVIEW_PHASES
