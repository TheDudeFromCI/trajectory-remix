"""Phase barriers: a run can wait before Assembly, Fast review or Critical review.

Each barrier is chosen per job when the run starts (`barriers` in the run's frozen config).
A gate is the point a trajectory reaches next:

- assembly: Draft is done, Assembly is next.
- fast: the graph is assembled, Fast review is next.
- critical: Fast passed, Critical review is next.

A trajectory that reaches an enabled, closed gate parks: its owner row becomes mode 'parked',
which does not count toward the active-trajectory cap, so later attempts can start. When no
attempt is pending and every owned trajectory is parked or held, the gate opens and the job
pauses, so models can be swapped. Resume releases the parked trajectories.

The number of opened gates is stored per job, so it survives a server restart.
"""

TABLE = """CREATE TABLE IF NOT EXISTS graph_job_phases (
 job_id TEXT PRIMARY KEY REFERENCES graph_jobs(id), phase INTEGER NOT NULL)"""
GATES = ("assembly", "fast", "critical")
OPEN_ALL = len(GATES)
REVIEW_PHASES = {"review"}


def phase(store, job):
    """How many gates are open: gates with a lower index let trajectories through."""
    with store.connect() as db:
        db.execute(TABLE)
        row = db.execute("SELECT phase FROM graph_job_phases WHERE job_id=?", (job,)).fetchone()
    return row[0] if row else 0


def advance(store, job, to=OPEN_ALL):
    with store.connect() as db:
        db.execute(TABLE)
        db.execute(
            "INSERT INTO graph_job_phases VALUES(?,?)"
            " ON CONFLICT(job_id) DO UPDATE SET phase=MAX(phase, excluded.phase)",
            (job, to),
        )


def validate(barriers):
    """The barriers a run asks for: a dict of gate name to boolean."""
    if barriers is None:
        return {}
    if not isinstance(barriers, dict) or set(barriers) - set(GATES):
        raise ValueError("barriers must name only: " + ", ".join(GATES))
    if any(type(value) is not bool for value in barriers.values()):
        raise ValueError("Each barrier must be true or false")
    return {gate: barriers.get(gate, False) for gate in GATES}


def configured(job):
    """The gates a job's frozen config enables (empty for manual reviews and old jobs)."""
    if job["kind"] != "run":
        return []
    barriers = job["config"]["config"].get("barriers") or {}
    return [gate for gate in GATES if barriers.get(gate)]


def closed(enabled, opened):
    """Enabled gates that have not opened yet."""
    return [gate for gate in enabled if GATES.index(gate) >= opened]


def gate(cp):
    """The gate this checkpoint's next step belongs to, or None."""
    if not cp or cp.get("manual"):
        return None
    if cp.get("phase") == "event":
        return "assembly" if "draft" in cp and not cp.get("assembled") else None
    if cp.get("phase") in REVIEW_PHASES and cp.get("tier") in GATES:
        return cp["tier"]
    return None


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
