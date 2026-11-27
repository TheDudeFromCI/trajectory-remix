"""Durable job/ownership boundary. Provider execution is injected, never imported."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from . import reconnect, trajectory_hold
from . import usage as usage_log
from .failure_reason import endpoint_failure
from .log_levels import check as check_level
from .log_levels import migrate as migrate_log_levels
from .models import GraphSource, canonical, digest
from .progress import observed_request, request_label
from .stage_progress import attach as attach_stage_progress
from .storage import GraphStore

TERMINAL = {"completed", "cancelled", "failed"}


class OwnershipConflict(ValueError):
    code = "ownership_conflict"


class Fenced(OwnershipConflict):
    code = "stale_worker"


SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, epoch INTEGER NOT NULL,
 config TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
 run_id TEXT, checkpoint TEXT NOT NULL DEFAULT '{}', artifact TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS graph_runs (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES graph_jobs(id),
 snapshot TEXT NOT NULL, digest TEXT NOT NULL, quota INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES graph_runs(id), ordinal INTEGER NOT NULL,
 trajectory_id TEXT NOT NULL UNIQUE, proposal TEXT NOT NULL, proposal_digest TEXT NOT NULL,
 state TEXT NOT NULL, outcome TEXT, UNIQUE(run_id,ordinal));
CREATE TABLE IF NOT EXISTS graph_owners (
 trajectory_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES graph_jobs(id),
 mode TEXT NOT NULL, epoch INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_detached (trajectory_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS graph_endpoints (
 id TEXT PRIMARY KEY, concurrency INTEGER NOT NULL, retries INTEGER NOT NULL,
 paused INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS graph_requests (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 endpoint TEXT NOT NULL REFERENCES graph_endpoints(id), job_id TEXT NOT NULL REFERENCES graph_jobs(id),
 trajectory_id TEXT NOT NULL, step TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL, retries INTEGER NOT NULL DEFAULT 0, retry_limit INTEGER NOT NULL,
 epoch INTEGER, lease TEXT,
 result TEXT, UNIQUE(job_id,step));
CREATE INDEX IF NOT EXISTS graph_queue ON graph_requests(endpoint,state,sequence);
CREATE TABLE IF NOT EXISTS graph_steps (
 job_id TEXT NOT NULL REFERENCES graph_jobs(id), step TEXT NOT NULL,
 request_id TEXT, commit_id TEXT NOT NULL, revision INTEGER NOT NULL,
 checkpoint TEXT NOT NULL, identity TEXT NOT NULL, PRIMARY KEY(job_id,step));
CREATE TABLE IF NOT EXISTS graph_checkpoints (
 job_id TEXT NOT NULL REFERENCES graph_jobs(id), trajectory_id TEXT NOT NULL,
 checkpoint TEXT NOT NULL, PRIMARY KEY(job_id,trajectory_id));
CREATE TABLE IF NOT EXISTS graph_log_cursors (
 job_id TEXT PRIMARY KEY REFERENCES graph_jobs(id), last INTEGER NOT NULL DEFAULT 0,
 lost_through INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS graph_job_logs (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES graph_jobs(id),
 text TEXT NOT NULL, bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_deletions (
 trajectory_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, commit_id TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS graph_frozen_run BEFORE UPDATE ON graph_runs
BEGIN SELECT RAISE(ABORT,'Run snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS graph_frozen_attempt BEFORE UPDATE OF proposal,proposal_digest,trajectory_id,run_id,ordinal ON graph_attempts
BEGIN SELECT RAISE(ABORT,'Attempt inputs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS graph_frozen_job BEFORE UPDATE OF config ON graph_jobs
BEGIN SELECT RAISE(ABORT,'Job inputs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS graph_frozen_request BEFORE UPDATE OF payload,endpoint,job_id,trajectory_id,step,retry_limit ON graph_requests
BEGIN SELECT RAISE(ABORT,'Request inputs are immutable'); END;
"""


def page(limit=100, offset=0):
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise ValueError("Invalid pagination")


@dataclass(frozen=True)
class Ticket:
    id: str
    job_id: str
    trajectory_id: str
    endpoint: str
    step: str
    epoch: int
    lease: str
    payload: dict


class JobService:
    """One service per project worker process; recover once on process startup.

    SQLite serializes claims/acceptance across connections. Driver calls and all
    expensive preparation happen outside transactions. Multi-process startup must
    be coordinated by the host (recovery intentionally invalidates old workers).
    """

    # Seconds between "Still waiting" log lines for a slow request; None keeps them off.
    heartbeat_interval = None

    def __init__(self, store: GraphStore, *, active_limit=20, recover=True, clock=time.time):
        if type(active_limit) is not int or active_limit < 1:
            raise ValueError("active_limit must be positive")
        self.store, self.active_limit, self.clock = store, active_limit, clock
        self._calls = {}
        with store.connect() as db:
            db.executescript(SCHEMA)
            reconnect.ensure_columns(db)
            migrate_log_levels(db)
        self.configure_endpoints()
        if recover:
            self.recover()

    def _interrupt_calls(self, job, trajectory=None):
        for (owner, source, _, _), task in list(self._calls.items()):
            if owner == job and (trajectory is None or trajectory == source):
                task.get_loop().call_soon_threadsafe(task.cancel)

    def configure_endpoints(self):
        settings = self.store.settings().settings
        with self.store.connect() as db:
            for ident, endpoint in settings.endpoints.items():
                db.execute(
                    """INSERT INTO graph_endpoints(id,concurrency,retries,reconnect_seconds)
                    VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET concurrency=excluded.concurrency,
                    retries=excluded.retries,reconnect_seconds=excluded.reconnect_seconds""",
                    (ident, endpoint.concurrency, endpoint.retries, endpoint.reconnect_seconds),
                )

    def _job(self, db, ident):
        row = db.execute("SELECT * FROM graph_jobs WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        return row

    def _notify(self, db, ident):
        row = self._job(db, ident)
        self.store._change(db, "job", ident, row["epoch"], ident)

    def _create(self, db, kind, config, total, run_id=None):
        ident = str(uuid4())
        db.execute(
            "INSERT INTO graph_jobs(id,kind,state,epoch,config,total,run_id,created) VALUES(?,?,'running',1,?,?,?,?)",
            (ident, kind, canonical(config), total, run_id, self.clock()),
        )
        db.execute("INSERT INTO graph_log_cursors(job_id) VALUES(?)", (ident,))
        self._notify(db, ident)
        return ident

    def create_job(self, kind, config=None, *, total=0):
        if type(total) is not int or total < 0:
            raise ValueError("Invalid total")
        config = json.loads(canonical(config or {}))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._create(db, kind, config, total)

    def create_run(self, config, proposals, *, settings_record=None):
        # Explicit proposals pin all later attempts now, never resample on resume.
        config, proposals = json.loads(canonical(config)), json.loads(canonical(proposals))
        if (
            not isinstance(config, dict)
            or not isinstance(proposals, list)
            or not proposals
            or not all(isinstance(p, dict) for p in proposals)
        ):
            raise ValueError("A run requires config and one object per attempt")
        settings = settings_record or self.store.settings()
        snapshot = dict(
            config=config,
            settings=settings.settings.model_dump(mode="json"),
            settings_revision=settings.revision,
            settings_commit=settings.commit_id,
        )
        run = str(uuid4())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._create(db, "run", snapshot, len(proposals), run)
            db.execute(
                "INSERT INTO graph_runs VALUES(?,?,?,?,?)",
                (run, job, canonical(snapshot), digest(snapshot), len(proposals)),
            )
            for ordinal, proposal in enumerate(proposals):
                db.execute(
                    "INSERT INTO graph_attempts VALUES(?,?,?,?,?,?,'pending',NULL)",
                    (
                        str(uuid4()),
                        run,
                        ordinal,
                        str(uuid4()),
                        canonical(proposal),
                        digest(proposal),
                    ),
                )
        return job

    def attempts(self, job, *, limit=100, offset=0):
        page(limit, offset)
        with self.store.connect() as db:
            run = self._job(db, job)["run_id"]
            return [
                dict(r) | {"proposal": json.loads(r["proposal"])}
                for r in db.execute(
                    "SELECT * FROM graph_attempts WHERE run_id=? ORDER BY ordinal LIMIT ? OFFSET ?",
                    (run, limit, offset),
                )
            ]

    def start_attempt(self, job, ordinal, source, *, expected_epoch=None):
        source = GraphSource.model_validate(source)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            j = self._job(db, job)
            if expected_epoch is not None:
                self._fence(db, job, expected_epoch)
            if j["state"] != "running":
                raise OwnershipConflict("Run is not running")
            attempt = db.execute(
                "SELECT * FROM graph_attempts WHERE run_id=? AND ordinal=?", (j["run_id"], ordinal)
            ).fetchone()
            if attempt is None:
                raise KeyError("Attempt not found")
            if attempt["state"] != "pending" or source.id != attempt["trajectory_id"]:
                raise OwnershipConflict("Attempt already started or source ID differs")
            if not self._has_attempt_slot(db):
                raise OwnershipConflict("Global active-trajectory cap reached")
            if db.execute(
                "SELECT 1 FROM graph_trajectory_revisions WHERE trajectory_id=?", (source.id,)
            ).fetchone():
                raise OwnershipConflict("Source identity already exists")
            self.store._commit(
                db,
                source,
                None,
                {"job_id": job, "attempt_id": attempt["id"]},
                settings_revision=json.loads(j["config"]).get("settings_revision"),
            )
            db.execute(
                "INSERT INTO graph_owners VALUES(?,?,'attached',?)", (source.id, job, j["epoch"])
            )
            db.execute("UPDATE graph_attempts SET state='active' WHERE id=?", (attempt["id"],))
            self._notify(db, job)
        return self.store.read(source.id)

    def _has_attempt_slot(self, db):
        active = db.execute("SELECT count(*) FROM graph_owners WHERE mode='attached'").fetchone()[0]
        return active < self.active_limit

    def park_attempt(self, job, epoch, trajectory):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._fence(db, job, epoch, trajectory)
            return (
                db.execute(
                    "UPDATE graph_owners SET mode='parked' WHERE trajectory_id=? AND mode='attached'",
                    (trajectory,),
                ).rowcount
                == 1
            )

    def activate_attempt(self, job, epoch, trajectory):
        """A parked or held attempt must reacquire a global slot before it can run."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._fence(db, job, epoch, trajectory)
            if not self._has_attempt_slot(db):
                return False
            return (
                db.execute(
                    "UPDATE graph_owners SET mode='attached' WHERE trajectory_id=?"
                    " AND mode IN ('parked','held')",
                    (trajectory,),
                ).rowcount
                == 1
            )

    def acquire(self, job, trajectory, expected_revision):
        """Shared exclusive manual review/repair slot, for detached sources only."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._acquire(db, job, trajectory, expected_revision)

    def _acquire(self, db, job, trajectory, expected_revision):
        j = self._job(db, job)
        if j["state"] != "running":
            raise OwnershipConflict("Job is not running")
        self.store._check_revision(trajectory, expected_revision, self.store._read(db, trajectory))
        self.store._check_owner(db, trajectory)
        db.execute("INSERT OR IGNORE INTO graph_detached VALUES(?)", (trajectory,))
        db.execute("INSERT INTO graph_owners VALUES(?,?,'manual',?)", (trajectory, job, j["epoch"]))
        return j["epoch"]

    @staticmethod
    def _reset_bulk(db, job, state):
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_bulk_items'"
        ).fetchone():
            db.execute(
                "UPDATE graph_bulk_items SET state=? WHERE job_id=? AND state='working'",
                (state, job),
            )

    def _fence(self, db, job, epoch, trajectory=None):
        j = self._job(db, job)
        if j["state"] != "running" or j["epoch"] != epoch:
            raise Fenced("Worker is no longer current")
        if trajectory is not None:
            owner = db.execute(
                "SELECT * FROM graph_owners WHERE trajectory_id=?", (trajectory,)
            ).fetchone()
            if owner is None or owner["job_id"] != job or owner["epoch"] != epoch:
                raise Fenced("Trajectory ownership changed")
        return j

    def accept(
        self,
        job,
        epoch,
        trajectory,
        step,
        source,
        expected_revision,
        checkpoint,
        *,
        request=None,
        outcome=None,
        bulk_position=None,
    ):
        source = GraphSource.model_validate(source)
        checkpoint = json.loads(canonical(checkpoint))
        if request is not None and not isinstance(request, Ticket):
            raise ValueError("Acceptance requires the claimed request Ticket")
        request_id = request.id if request else None
        identity = digest(
            dict(
                trajectory=trajectory,
                source=source.to_dict(),
                checkpoint=checkpoint,
                request=request_id,
                lease=request.lease if request else None,
                outcome=outcome,
                bulk_position=bulk_position,
            )
        )
        if source.id != trajectory:
            raise ValueError("Source identity differs")
        if outcome not in (None, "pass", "fail", "abandoned"):
            raise ValueError("Invalid detached outcome")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM graph_steps WHERE job_id=? AND step=?", (job, step)
            ).fetchone()
            if prior:
                # A duplicate acknowledgement returns the original publication.
                if prior["identity"] != identity:
                    raise ValueError("Idempotency key reused with different acceptance")
                return dict(prior)
            current_job = self._fence(db, job, epoch, trajectory)
            if request is not None:
                r = db.execute("SELECT * FROM graph_requests WHERE id=?", (request_id,)).fetchone()
                if (
                    r is None
                    or r["job_id"] != job
                    or r["trajectory_id"] != trajectory
                    or r["step"] != step
                    or r["state"] != "ready"
                    or r["epoch"] != epoch
                    or r["lease"] != request.lease
                ):
                    raise Fenced("Request is not ready for acceptance")
            old = self.store._read(db, trajectory)
            self.store._check_revision(trajectory, expected_revision, old)
            configuration = json.loads(current_job["config"])
            settings_revision = configuration.get(
                "settings_revision", configuration.get("options", {}).get("settings_revision")
            )
            record = self.store._commit(
                db,
                source,
                old,
                {"job_id": job, "step": step},
                settings_revision=settings_revision,
            )
            db.execute(
                "INSERT INTO graph_steps VALUES(?,?,?,?,?,?,?)",
                (
                    job,
                    step,
                    request_id,
                    record.commit_id,
                    record.revision,
                    canonical(checkpoint),
                    identity,
                ),
            )
            db.execute(
                """INSERT INTO graph_checkpoints VALUES(?,?,?) ON CONFLICT(job_id,trajectory_id)
                DO UPDATE SET checkpoint=excluded.checkpoint""",
                (job, trajectory, canonical(checkpoint)),
            )
            db.execute(
                "UPDATE graph_jobs SET checkpoint=? WHERE id=?", (canonical(checkpoint), job)
            )
            if request is not None:
                db.execute(
                    "UPDATE graph_requests SET state='accepted',result=NULL WHERE id=?",
                    (request_id,),
                )
            if trajectory_hold.held(checkpoint, epoch):
                if current_job["run_id"]:
                    db.execute(
                        "UPDATE graph_owners SET mode='held' WHERE trajectory_id=?", (trajectory,)
                    )
                if request is None:
                    # A local failure can leave a request ready before its output is accepted.
                    # Resume starts a new step from the held checkpoint.
                    db.execute(
                        """UPDATE graph_requests SET state='cancelled',result=NULL
                        WHERE job_id=? AND trajectory_id=? AND state IN ('queued','running','ready')""",
                        (job, trajectory),
                    )
            if outcome is not None:
                self._detach(db, job, trajectory, outcome)
            if bulk_position is not None:
                item = db.execute(
                    "SELECT * FROM graph_bulk_items WHERE job_id=? AND position=?",
                    (job, bulk_position),
                ).fetchone()
                if (
                    item is None
                    or item["trajectory_id"] != trajectory
                    or item["state"] != "working"
                ):
                    raise Fenced("Review target is no longer current")
                before = self.store._read(db, trajectory, item["revision"]).source.to_dict()
                after = record.source.to_dict()
                delta = (
                    dict(
                        id=trajectory,
                        before=before,
                        after=after,
                        expected_revision=record.revision,
                        expected_commit=record.commit_id,
                    )
                    if before != after
                    else None
                )
                db.execute(
                    "UPDATE graph_bulk_items SET delta=?,state=? WHERE job_id=? AND position=?",
                    (
                        canonical(delta) if delta else None,
                        ("changed" if delta else "unchanged") if outcome else "working",
                        job,
                        bulk_position,
                    ),
                )
                if outcome:
                    db.execute("UPDATE graph_jobs SET progress=progress+1 WHERE id=?", (job,))
                    if not db.execute(
                        "SELECT 1 FROM graph_bulk_items WHERE job_id=? AND state IN ('pending','working')",
                        (job,),
                    ).fetchone():
                        db.execute("UPDATE graph_jobs SET state='completed' WHERE id=?", (job,))
            self._notify(db, job)
            result = dict(
                db.execute(
                    "SELECT * FROM graph_steps WHERE job_id=? AND step=?", (job, step)
                ).fetchone()
            )
            endpoint = checkpoint.get("exchange_limit_endpoint")
            if endpoint is not None:
                hits = db.execute(
                    """SELECT count(*) FROM (
                           SELECT 1 FROM graph_checkpoints WHERE job_id=?
                           AND json_extract(checkpoint, '$.exchange_limit_endpoint')=? LIMIT 3
                       )""",
                    (job, endpoint),
                ).fetchone()[0]
                result["repeated_exchange_limit"] = hits == 2
            return result

    def _detach(self, db, job, trajectory, outcome):
        db.execute("DELETE FROM graph_owners WHERE trajectory_id=? AND job_id=?", (trajectory, job))
        db.execute("INSERT OR IGNORE INTO graph_detached VALUES(?)", (trajectory,))
        db.execute(
            "UPDATE graph_requests SET state='cancelled',result=NULL WHERE job_id=? AND trajectory_id=? AND state IN ('queued','running','ready')",
            (job, trajectory),
        )
        db.execute(
            "UPDATE graph_attempts SET state='detached',outcome=? WHERE trajectory_id=? AND state='active'",
            (outcome, trajectory),
        )
        j = self._job(db, job)
        if j["run_id"]:
            count = db.execute(
                "SELECT count(*) FROM graph_attempts WHERE run_id=? AND state='detached'",
                (j["run_id"],),
            ).fetchone()[0]
            db.execute("UPDATE graph_jobs SET progress=? WHERE id=?", (count, job))
            if count == j["total"]:
                db.execute("UPDATE graph_jobs SET state='completed' WHERE id=?", (job,))

    def detach(self, job, trajectory):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            j = self._job(db, job)
            if not db.execute(
                "SELECT 1 FROM graph_owners WHERE job_id=? AND trajectory_id=?", (job, trajectory)
            ).fetchone():
                raise OwnershipConflict("Job does not own trajectory")
            self._detach(db, job, trajectory, "abandoned")
            if j["kind"] == "bulk":
                # Manual review/repair uses bulk items rather than run attempts.
                # Pause/recovery can leave an owned item pending. Settle either
                # state and preserve all edits committed before the detach fence.
                item = db.execute(
                    "SELECT * FROM graph_bulk_items WHERE job_id=? AND trajectory_id=? AND state IN ('pending','working')",
                    (job, trajectory),
                ).fetchone()
                if item is not None:
                    current = self.store._read(db, trajectory)
                    before = self.store._read(db, trajectory, item["revision"]).source.to_dict()
                    after = current.source.to_dict()
                    delta = (
                        dict(
                            id=trajectory,
                            before=before,
                            after=after,
                            expected_revision=current.revision,
                            expected_commit=current.commit_id,
                        )
                        if before != after
                        else None
                    )
                    db.execute(
                        "UPDATE graph_bulk_items SET state='detached',warning=?,delta=? WHERE job_id=? AND position=?",
                        (
                            "Detached manually; committed edits retained, no review verdict implied",
                            canonical(delta) if delta else None,
                            job,
                            item["position"],
                        ),
                    )
                    db.execute("UPDATE graph_jobs SET progress=progress+1 WHERE id=?", (job,))
                    if not db.execute(
                        "SELECT 1 FROM graph_bulk_items WHERE job_id=? AND state IN ('pending','working')",
                        (job,),
                    ).fetchone():
                        db.execute("UPDATE graph_jobs SET state='completed' WHERE id=?", (job,))
            self._notify(db, job)
        self._interrupt_calls(job, trajectory)

    def control(self, job, action):
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError("Invalid job control")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            j = self._job(db, job)
            if j["state"] in TERMINAL:
                if action == "cancel" and j["state"] == "cancelled":
                    return self._decode_job(j)
                raise OwnershipConflict("Job is terminal")
            if action == "resume":
                if j["state"] != "paused":
                    raise OwnershipConflict("Job is not paused")
                db.execute("UPDATE graph_jobs SET state='running' WHERE id=?", (job,))
            else:
                state = "cancelled" if action == "cancel" else "paused"
                db.execute("UPDATE graph_jobs SET state=?,epoch=epoch+1 WHERE id=?", (state, job))
                db.execute(
                    "UPDATE graph_requests SET state=?,result=NULL WHERE job_id=? AND state IN ('queued','running','ready')",
                    ("cancelled" if action == "cancel" else "queued", job),
                )
                if action == "cancel":
                    for row in db.execute(
                        "SELECT trajectory_id FROM graph_owners WHERE job_id=?", (job,)
                    ).fetchall():
                        self._detach(db, job, row[0], "cancelled")
                    db.execute(
                        "UPDATE graph_attempts SET state='cancelled' WHERE run_id=? AND state='pending'",
                        (j["run_id"],),
                    )
                    # _detach may have completed the quota; cancellation wins.
                    db.execute("UPDATE graph_jobs SET state='cancelled' WHERE id=?", (job,))
                else:
                    db.execute("UPDATE graph_owners SET epoch=epoch+1 WHERE job_id=?", (job,))
                self._reset_bulk(db, job, "cancelled" if action == "cancel" else "pending")
            self._notify(db, job)
        if action != "resume":
            self._interrupt_calls(job)
        return self.inspect(job)

    def finish(self, job, epoch):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            j = self._fence(db, job, epoch)
            if db.execute("SELECT 1 FROM graph_owners WHERE job_id=?", (job,)).fetchone():
                raise OwnershipConflict("Job still owns trajectories")
            if j["run_id"] and j["progress"] != j["total"]:
                raise OwnershipConflict("Run attempt quota has not detached")
            db.execute("UPDATE graph_jobs SET state='completed',progress=total WHERE id=?", (job,))
            self._notify(db, job)

    def checkpoint(self, job, trajectory):
        with self.store.connect() as db:
            self._job(db, job)
            row = db.execute(
                "SELECT checkpoint FROM graph_checkpoints WHERE job_id=? AND trajectory_id=?",
                (job, trajectory),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def recover(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Older runs kept held attempts attached. Preserve the checkpoint while freeing
            # its slot so a recovered job can admit other work before the hold is retried.
            db.execute(
                """UPDATE graph_owners AS o SET mode='held' WHERE mode='attached' AND EXISTS (
                SELECT 1 FROM graph_checkpoints c WHERE c.job_id=o.job_id
                AND c.trajectory_id=o.trajectory_id AND json_type(c.checkpoint, '$.held')='object')"""
            )
            db.execute(
                """UPDATE graph_requests AS r SET state='cancelled',result=NULL
                WHERE state IN ('queued','running','ready') AND EXISTS (
                SELECT 1 FROM graph_owners o WHERE o.job_id=r.job_id
                AND o.trajectory_id=r.trajectory_id AND o.mode='held')"""
            )
            jobs = db.execute(
                "SELECT id FROM graph_jobs WHERE state IN ('running','paused')"
            ).fetchall()
            for row in jobs:
                db.execute(
                    "UPDATE graph_jobs SET state='paused',epoch=epoch+1 WHERE id=?", (row[0],)
                )
                db.execute("UPDATE graph_owners SET epoch=epoch+1 WHERE job_id=?", (row[0],))
                self._reset_bulk(db, row[0], "pending")
                self._notify(db, row[0])
            # All unaccepted bodies are disposable. Original request payloads survive.
            db.execute(
                "UPDATE graph_requests SET state='queued',result=NULL WHERE state IN ('running','ready')"
            )

    def enqueue(self, job, trajectory, endpoint, step, payload, *, expected_epoch=None):
        payload = canonical(payload)
        if not isinstance(json.loads(payload), dict) or not step:
            raise ValueError("Request requires object payload and stable step")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            j = self._job(db, job)
            self._fence(
                db, job, j["epoch"] if expected_epoch is None else expected_epoch, trajectory
            )
            old = db.execute(
                "SELECT * FROM graph_requests WHERE job_id=? AND step=?", (job, step)
            ).fetchone()
            if old:
                if (old["trajectory_id"], old["endpoint"], old["payload"]) != (
                    trajectory,
                    endpoint,
                    payload,
                ):
                    raise ValueError("Idempotency key reused with different request")
                return old["id"]
            endpoint_config = db.execute(
                "SELECT retries FROM graph_endpoints WHERE id=?", (endpoint,)
            ).fetchone()
            if endpoint_config is None:
                raise KeyError("Endpoint not found")
            retry_limit = endpoint_config[0]
            if j["run_id"]:
                pinned = json.loads(j["config"])["settings"]["endpoints"].get(endpoint)
                if pinned is None:
                    raise ValueError("Endpoint is not in the frozen run settings")
                retry_limit = pinned["retries"]
            elif json.loads(j["config"]).get("options", {}).get("settings"):
                retry_limit = json.loads(j["config"])["options"]["settings"]["endpoints"][endpoint][
                    "retries"
                ]
            ident = str(uuid4())
            db.execute(
                "INSERT INTO graph_requests(id,endpoint,job_id,trajectory_id,step,payload,state,retry_limit) VALUES(?,?,?,?,?,?,'queued',?)",
                (ident, endpoint, job, trajectory, step, payload, retry_limit),
            )
            return ident

    def claim(self, endpoint):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            ep = db.execute("SELECT * FROM graph_endpoints WHERE id=?", (endpoint,)).fetchone()
            if ep is None:
                raise KeyError("Endpoint not found")
            active = {
                r[0]
                for r in db.execute(
                    "SELECT id FROM graph_requests WHERE endpoint=? AND state='running'",
                    (endpoint,),
                )
            }
            active.update(
                key[2]
                for key, task in list(self._calls.items())
                if key[3] == endpoint and not task.done()
            )
            if ep["paused"] or len(active) >= ep["concurrency"]:
                return None
            row = db.execute(
                """SELECT r.*,j.epoch AS owner_epoch FROM graph_requests r
                JOIN graph_jobs j ON j.id=r.job_id JOIN graph_owners o ON o.trajectory_id=r.trajectory_id
                AND o.job_id=r.job_id WHERE r.endpoint=? AND r.state='queued' AND j.state='running'
                AND o.mode IN ('attached','manual')
                ORDER BY r.sequence LIMIT 1""",
                (endpoint,),
            ).fetchone()
            if row is None:
                return None
            # Do not overtake an earlier runnable item blocked by its source slot.
            if db.execute(
                "SELECT 1 FROM graph_requests WHERE trajectory_id=? AND state IN ('running','ready')",
                (row["trajectory_id"],),
            ).fetchone():
                return None
            if any(
                key[1] == row["trajectory_id"] and not task.done()
                for key, task in list(self._calls.items())
            ):
                return None
            lease = str(uuid4())
            db.execute(
                "UPDATE graph_requests SET state='running',epoch=?,lease=? WHERE id=?",
                (row["owner_epoch"], lease, row["id"]),
            )
            return Ticket(
                row["id"],
                row["job_id"],
                row["trajectory_id"],
                endpoint,
                row["step"],
                row["owner_epoch"],
                lease,
                json.loads(row["payload"]),
            )

    def _ticket(self, db, ticket):
        self._fence(db, ticket.job_id, ticket.epoch, ticket.trajectory_id)
        row = db.execute("SELECT * FROM graph_requests WHERE id=?", (ticket.id,)).fetchone()
        if (
            row is None
            or row["state"] != "running"
            or row["epoch"] != ticket.epoch
            or row["lease"] != ticket.lease
        ):
            raise Fenced("Request is no longer current")
        return row

    def request_result(self, ticket, result):
        result = canonical(result)  # Complete response only; no streaming sink exists.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ticket(db, ticket)
            db.execute(
                "UPDATE graph_requests SET state='ready',result=? WHERE id=?", (result, ticket.id)
            )

    def result(self, ticket):
        """Read a complete disposable body for validation before atomic acceptance."""
        with self.store.connect() as db:
            self._fence(db, ticket.job_id, ticket.epoch, ticket.trajectory_id)
            row = db.execute("SELECT * FROM graph_requests WHERE id=?", (ticket.id,)).fetchone()
            if (
                row is None
                or row["state"] != "ready"
                or row["epoch"] != ticket.epoch
                or row["lease"] != ticket.lease
            ):
                raise Fenced("Request is not ready")
            return json.loads(row["result"])

    def retry(self, ticket):
        """Return True while retaining this item's running endpoint/source slot."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._ticket(db, ticket)
            if row["retries"] < row["retry_limit"]:
                db.execute("UPDATE graph_requests SET retries=retries+1 WHERE id=?", (ticket.id,))
                return True
            db.execute("UPDATE graph_endpoints SET paused=1 WHERE id=?", (ticket.endpoint,))
            reconnect.schedule(db, ticket.endpoint, self.clock())
            db.execute(
                "UPDATE graph_requests SET state='queued',result=NULL WHERE id=?", (ticket.id,)
            )
            self._notify(db, ticket.job_id)
            return False

    def reconnect_due(self):
        """Resume endpoints whose automatic reconnect delay has elapsed."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return reconnect.resume_due(db, self.clock())

    def resume_endpoint(self, endpoint):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM graph_endpoints WHERE id=?", (endpoint,)).fetchone():
                raise KeyError("Endpoint not found")
            db.execute("UPDATE graph_endpoints SET paused=0,resume_at=NULL WHERE id=?", (endpoint,))
            db.execute(
                "UPDATE graph_requests SET retries=0 WHERE endpoint=? AND state='queued'",
                (endpoint,),
            )

    async def execute_one(self, endpoint, driver):
        """driver(ticket) is ONE complete API call; it disables its own retries.

        Partial text stays solely in the driver's disposable local buffer. Return
        complete JSON data; handoff 06 validates and accepts it separately.
        """
        ticket = self.claim(endpoint)
        if ticket is None:
            return None
        attempt = 0
        while True:
            attempt += 1
            try:
                result, elapsed = await observed_request(self, ticket, driver, attempt)
            except asyncio.CancelledError:
                # Host shutdown/pause fences this item; never log response text.
                try:
                    with self.store.connect() as db:
                        self._fence(db, ticket.job_id, ticket.epoch, ticket.trajectory_id)
                except Fenced:
                    pass
                else:
                    self.control(ticket.job_id, "pause")
                self.progress(
                    ticket.job_id,
                    "Request interrupted; no response accepted: " + request_label(ticket),
                    "warning",
                )
                raise
            except Exception as error:
                retrying = self.retry(ticket)
                reason = endpoint_failure(
                    error, ticket.endpoint, ticket.payload.get("endpoint_config")
                )
                self.progress(
                    ticket.job_id,
                    f"Request failed ({type(error).__name__}): {reason}; "
                    + (
                        "retrying in the same queue slot: "
                        if retrying
                        else "endpoint paused after retry exhaustion; manual Resume required: "
                    )
                    + request_label(ticket),
                    "warning" if retrying else "error",
                )
                if retrying:
                    continue
                return None
            self.request_result(ticket, result)
            counts = usage_log.describe(usage_log.record(self.store, ticket, result, elapsed))
            self.progress(
                ticket.job_id,
                f"Response received ({elapsed:.1f}s); validation pending: "
                + request_label(ticket)
                + (" " + counts if counts else ""),
            )
            return ticket

    @staticmethod
    def _decode_job(row):
        result = dict(row)
        for key in ("config", "checkpoint"):
            result[key] = json.loads(result[key])
        return result

    def summaries(self, *, limit, offset):
        with self.store.connect() as db:
            db.execute("BEGIN")
            now = self.clock()
            rows = [
                {**dict(row), "age": max(0.0, now - row["created"])}  # Seconds, server clock.
                for row in db.execute(
                    "SELECT id,kind,json_extract(config,'$.action') AS action,state,progress,total,artifact,created FROM graph_jobs ORDER BY created DESC,id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]
            attach_stage_progress(db, rows)
            return rows

    def inspect(self, job, *, include_config=True):
        with self.store.connect() as db:
            if include_config:
                result = self._decode_job(self._job(db, job))
            else:
                db.execute("BEGIN")
                row = db.execute(
                    "SELECT id,kind,json_extract(config,'$.action') AS action,state,epoch,run_id,progress,total,artifact,created FROM graph_jobs WHERE id=?",
                    (job,),
                ).fetchone()
                if row is None:
                    raise KeyError(job)
                result = dict(row)
                attach_stage_progress(db, [result])
            result["requests"] = [
                dict(r)
                for r in db.execute(
                    "SELECT state,count(*) AS count FROM graph_requests WHERE job_id=? GROUP BY state",
                    (job,),
                )
            ]
            result["paused_endpoints"] = [
                r[0]
                for r in db.execute(
                    """SELECT DISTINCT e.id FROM graph_endpoints e JOIN graph_requests r
                ON r.endpoint=e.id WHERE r.job_id=? AND e.paused=1 AND r.state='queued' """,
                    (job,),
                )
            ]
            result["attempt_states"] = [
                dict(r)
                for r in db.execute(
                    "SELECT state,outcome,count(*) AS count FROM graph_attempts WHERE run_id=? GROUP BY state,outcome",
                    (result["run_id"],),
                )
            ]
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_bulk_items'"
            ).fetchone():
                result["result_states"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT state,count(*) AS count FROM graph_bulk_items WHERE job_id=? GROUP BY state",
                        (job,),
                    )
                ]
            return result

    def listing(self, *, limit=100, offset=0):
        page(limit, offset)
        with self.store.connect() as db:
            return [
                self._decode_job(r)
                for r in db.execute(
                    "SELECT * FROM graph_jobs ORDER BY created,id LIMIT ? OFFSET ?", (limit, offset)
                )
            ]

    def changes(self, after=0, *, limit=100):
        page(limit, after)
        with self.store.connect() as db:
            lo, hi = db.execute(
                "SELECT min(cursor),max(cursor) FROM graph_project_changes"
            ).fetchone()
            return dict(
                resync=bool(lo and after < lo - 1),
                latest=hi or 0,
                items=[
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM graph_project_changes WHERE cursor>? ORDER BY cursor LIMIT ?",
                        (after, limit),
                    )
                ],
            )

    def progress(self, job, text, level="info"):
        stamp = datetime.fromtimestamp(self.clock(), UTC).isoformat(timespec="seconds")
        return self.append_log(job, f"[{stamp}] {text}", level=level)

    def append_log(self, job, text, *, budget=None, level="info"):
        if not isinstance(text, str):
            raise ValueError("Log text must be a string")
        check_level(level)
        # budget is bytes for fake-budget tests; production uses project MiB.
        if budget is None:
            cap = self.store.settings().settings.log_cache_mib
            budget = None if cap is None else cap * 1024 * 1024
        if budget is not None and (type(budget) is not int or budget < 0):
            raise ValueError("Invalid log budget")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._job(db, job)
            cursor = db.execute(
                "INSERT INTO graph_job_logs(job_id,text,bytes,level) VALUES(?,?,?,?)",
                (job, text, len(text.encode()), level),
            ).lastrowid
            db.execute("UPDATE graph_log_cursors SET last=? WHERE job_id=?", (cursor, job))
            if budget is not None:
                total = db.execute("SELECT coalesce(sum(bytes),0) FROM graph_job_logs").fetchone()[
                    0
                ]
                for row in db.execute(
                    "SELECT sequence,job_id,bytes FROM graph_job_logs ORDER BY sequence"
                ).fetchall():
                    if total <= budget:
                        break
                    db.execute("DELETE FROM graph_job_logs WHERE sequence=?", (row[0],))
                    db.execute(
                        "UPDATE graph_log_cursors SET lost_through=max(lost_through,?) WHERE job_id=?",
                        (row[0], row[1]),
                    )
                    total -= row[2]
            return cursor

    def logs(self, job, *, after=0, through=None, limit=100):
        page(limit, after)
        with self.store.connect() as db:
            self._job(db, job)
            state = db.execute("SELECT * FROM graph_log_cursors WHERE job_id=?", (job,)).fetchone()
            return dict(
                last=state["last"],
                truncated=after < state["lost_through"],
                items=[
                    dict(r)
                    for r in db.execute(
                        "SELECT sequence,text,level FROM graph_job_logs WHERE job_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
                        (job, after, through if through is not None else state["last"], limit),
                    )
                ],
            )

    def clear_logs(self, job=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if job is not None:
                self._job(db, job)
                db.execute("DELETE FROM graph_job_logs WHERE job_id=?", (job,))
                db.execute("UPDATE graph_log_cursors SET lost_through=last WHERE job_id=?", (job,))
            else:
                db.execute("DELETE FROM graph_job_logs")
                db.execute("UPDATE graph_log_cursors SET lost_through=last")

    def log_usage(self):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT count(*),coalesce(sum(bytes),0) FROM graph_job_logs"
            ).fetchone()
            return dict(
                entries=row[0], bytes=row[1], cap_mib=self.store.settings().settings.log_cache_mib
            )
