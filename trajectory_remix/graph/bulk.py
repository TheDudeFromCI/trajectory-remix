"""Frozen search-wide bulk targets and settled, changed-only browser history."""

from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

from .jobs import TERMINAL, Fenced, OwnershipConflict, page
from .models import GraphSource, canonical
from .storage import RevisionConflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_bulk_items (
 job_id TEXT NOT NULL REFERENCES graph_jobs(id), position INTEGER NOT NULL,
 trajectory_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
 warning TEXT, delta TEXT, PRIMARY KEY(job_id,position), UNIQUE(job_id,trajectory_id));
"""


class BulkService:
    def __init__(self, jobs):
        self.jobs, self.store = jobs, jobs.store
        with self.store.connect() as db:
            db.executescript(SCHEMA)

    @staticmethod
    def _where(query):
        # Small explicit graph query grammar, independent of legacy replay search.
        # The studio may extend this shared selector, never pass a page of IDs.
        if not isinstance(query, dict) or set(query) - {
            "stage",
            "split",
            "text",
            "tool",
            "comments",
        }:
            raise ValueError("Unknown graph search fields")
        clauses, args = [], []
        for key in ("stage", "split"):
            if key in query:
                value = query[key]
                if key == "stage" and (type(value) is not int or value not in range(-3, 4)):
                    raise ValueError("Invalid stage")
                if key == "split" and value not in ("Train", "Development", "Test", "Auto"):
                    raise ValueError("Invalid split")
                clauses.append(f"json_extract(r.source,'$.metadata.{key}')=?")
                args.append(value)
        if "text" in query:
            if not isinstance(query["text"], str):
                raise ValueError("Text search requires a string")
            clauses.append("instr(lower(r.source),lower(?))>0")
            args.append(query["text"])
        if "tool" in query:
            if not isinstance(query["tool"], str):
                raise ValueError("Tool search requires a string")
            clauses.append(
                "EXISTS(SELECT 1 FROM graph_tool_usage u WHERE u.trajectory_id=t.id AND u.tool_id=?)"
            )
            args.append(query["tool"])
        if "comments" in query:
            # Only "open" is meaningful: trajectories with at least one open comment.
            if query["comments"] != "open":
                raise ValueError('The comments filter only accepts "open"')
            clauses.append(
                "EXISTS(SELECT 1 FROM graph_comments c WHERE c.trajectory_id=t.id"
                " AND c.status='open')"
            )
        return " AND ".join(clauses) or "1", args

    def start(self, query, action, config=None):
        query, config = deepcopy(query), deepcopy(config or {})
        if action not in {"stage", "split", "metrics", "delete", "review_repair"}:
            raise ValueError("Unknown bulk action")
        if action == "stage" and (
            set(config) != {"value"}
            or type(config["value"]) is not int
            or config["value"] not in range(-3, 4)
        ):
            raise ValueError("Stage must be -3 through 3")
        if action == "split" and (
            set(config) != {"value"}
            or config["value"] not in {"Train", "Development", "Test", "Auto"}
        ):
            raise ValueError("Invalid split")
        where, args = self._where(query)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self.jobs._create(db, "bulk", dict(query=query, action=action, options=config), 0)
            # INSERT SELECT freezes all matches in SQLite, independent of UI pages.
            db.execute(
                f"""INSERT INTO graph_bulk_items(job_id,position,trajectory_id,revision,state)
                SELECT ?,row_number() OVER (ORDER BY t.id)-1,t.id,t.revision,'pending'
                FROM graph_trajectories t JOIN graph_trajectory_revisions r
                ON r.trajectory_id=t.id AND r.revision=t.revision WHERE {where}""",
                [job, *args],
            )
            db.execute(
                "UPDATE graph_jobs SET total=(SELECT count(*) FROM graph_bulk_items WHERE job_id=?) WHERE id=?",
                (job, job),
            )
            self.jobs._notify(db, job)
        return job

    def results(self, job, *, limit=100, offset=0):
        page(limit, offset)
        with self.store.connect() as db:
            self.jobs._job(db, job)
            return [
                dict(r) | {"delta": json.loads(r["delta"]) if r["delta"] else None}
                for r in db.execute(
                    "SELECT * FROM graph_bulk_items WHERE job_id=? ORDER BY position LIMIT ? OFFSET ?",
                    (job, limit, offset),
                )
            ]

    def history(self, job, *, limit=100, offset=0):
        if self.jobs.inspect(job)["state"] not in TERMINAL:
            raise OwnershipConflict("History is available after settlement")
        page(limit, offset)
        with self.store.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT delta FROM graph_bulk_items WHERE job_id=? AND delta IS NOT NULL ORDER BY position LIMIT ? OFFSET ?",
                    (job, limit, offset),
                )
            ]

    def _settle_item(self, db, job, position, state, warning=None, delta=None):
        settled = db.execute(
            "UPDATE graph_bulk_items SET state=?,warning=?,delta=? WHERE job_id=? AND position=? AND state IN ('pending','working')",
            (state, warning, canonical(delta) if delta else None, job, position),
        )
        if settled.rowcount:
            db.execute("UPDATE graph_jobs SET progress=progress+1 WHERE id=?", (job,))
            self.jobs._notify(db, job)

    def _delete(self, db, record):
        ident = record.source.id
        db.execute("DELETE FROM graph_tool_usage WHERE trajectory_id=?", (ident,))
        db.execute("DELETE FROM graph_trajectories WHERE id=?", (ident,))
        commit = str(uuid4())
        db.execute(
            "INSERT OR REPLACE INTO graph_deletions VALUES(?,?,?)", (ident, record.revision, commit)
        )
        self.store._change(db, "delete", ident, record.revision, commit)
        return commit

    def advance(self, job, handlers=None, *, batch_size=25):
        """Process a bounded chunk. Handler(record, options) runs outside SQLite.

        metrics returns a dict of newly calculated values; only absent/null
        metrics are filled. review_repair returns a complete GraphSource/dict.
        Drivers doing API work must enqueue through JobService, then use the
        owned commit interface; this synchronous hook is for deterministic local
        calculations or completed review results, never nested live model calls.
        """
        page(batch_size)
        handlers = handlers or {}
        j = self.jobs.inspect(job)
        if j["kind"] != "bulk":
            raise ValueError("Not a bulk job")
        if j["state"] != "running":
            return j
        epoch, config = j["epoch"], j["config"]
        action, options = config["action"], config["options"]
        if action in {"metrics", "review_repair"} and action not in handlers:
            raise ValueError(f"A {action} handler is required")
        with self.store.connect() as db:
            pending = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM graph_bulk_items WHERE job_id=? AND state='pending' ORDER BY position LIMIT ?",
                    (job, batch_size),
                )
            ]
        for item in pending:
            ident, position = item["trajectory_id"], item["position"]
            acquired = False
            try:
                # Acquire exclusive ownership before handing a source to a driver.
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.jobs._fence(db, job, epoch)
                    state = db.execute(
                        "SELECT state FROM graph_bulk_items WHERE job_id=? AND position=?",
                        (job, position),
                    ).fetchone()[0]
                    if state != "pending":
                        continue
                    owner = db.execute(
                        "SELECT * FROM graph_owners WHERE trajectory_id=?", (ident,)
                    ).fetchone()
                    if (
                        owner is not None
                        and owner["job_id"] == job
                        and owner["epoch"] == epoch
                        and owner["mode"] == "manual"
                    ):
                        self.store._check_revision(
                            ident, item["revision"], self.store._read(db, ident)
                        )
                    else:
                        self.jobs._acquire(db, job, ident, item["revision"])
                    db.execute(
                        "UPDATE graph_bulk_items SET state='working' WHERE job_id=? AND position=?",
                        (job, position),
                    )
                    acquired = True
                record = self.store.read(ident)
                before = record.source.to_dict()
                after = deepcopy(before)
                if action in {"stage", "split"}:
                    after.setdefault("metadata", {})[action] = options["value"]
                elif action == "metrics":
                    values = handlers[action](record, deepcopy(options))
                    metrics = after.setdefault("metadata", {}).setdefault("metrics", {})
                    if not isinstance(values, dict) or not isinstance(metrics, dict):
                        raise ValueError("Metrics must be objects")
                    for key, value in values.items():
                        if metrics.get(key) is None:
                            metrics[key] = value
                elif action == "review_repair":
                    result = handlers[action](record, deepcopy(options))
                    after = GraphSource.model_validate(result).to_dict()
                if action != "delete":
                    prepared = GraphSource.model_validate(after)
                    if prepared.id != ident:
                        raise ValueError("Handler changed trajectory identity")
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.jobs._fence(db, job, epoch, ident)
                    current_item = db.execute(
                        "SELECT state FROM graph_bulk_items WHERE job_id=? AND position=?",
                        (job, position),
                    ).fetchone()
                    if current_item[0] != "working":
                        raise Fenced("Bulk item already settled")
                    current = self.store._read(db, ident)
                    self.store._check_revision(ident, item["revision"], current)
                    delta = None
                    if action == "delete":
                        commit = self._delete(db, current)
                        delta = dict(
                            id=ident,
                            before=before,
                            after=None,
                            expected_revision=current.revision,
                            expected_commit=commit,
                        )
                    elif before != after:
                        new = self.store._commit(
                            db, prepared, current, {"job_id": job, "bulk": position}
                        )
                        delta = dict(
                            id=ident,
                            before=before,
                            after=new.source.to_dict(),
                            expected_revision=new.revision,
                            expected_commit=new.commit_id,
                        )
                    if acquired:
                        db.execute(
                            "DELETE FROM graph_owners WHERE job_id=? AND trajectory_id=?",
                            (job, ident),
                        )
                    self._settle_item(
                        db, job, position, "changed" if delta else "unchanged", delta=delta
                    )
            except Fenced:
                break
            except (OwnershipConflict, RevisionConflict, KeyError, ValueError) as error:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        self.jobs._fence(db, job, epoch)
                    except Fenced:
                        break
                    if acquired:
                        db.execute(
                            "DELETE FROM graph_owners WHERE job_id=? AND trajectory_id=?",
                            (job, ident),
                        )
                    self._settle_item(
                        db, job, position, "skipped", f"{type(error).__name__}: {error}"
                    )
            except Exception:
                # Unknown driver failure is resumable, without retaining body text.
                self.jobs.control(job, "pause")
                raise
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.jobs._job(db, job)
            if (
                current["state"] == "running"
                and current["epoch"] == epoch
                and not db.execute(
                    "SELECT 1 FROM graph_bulk_items WHERE job_id=? AND state IN ('pending','working')",
                    (job,),
                ).fetchone()
            ):
                db.execute("UPDATE graph_jobs SET state='completed' WHERE id=?", (job,))
                self.jobs._notify(db, job)
        return self.jobs.inspect(job)

    def restore(self, deltas, *, undo=True):
        """Browser sends saved changed-entry deltas. Return inverse deltas/warnings.

        No driver/queue/model reference exists here. Apply bounded entries with
        per-entry revision AND commit guards; partial conflicts are explicit.
        """
        if not isinstance(deltas, list) or not 1 <= len(deltas) <= 1000:
            raise ValueError("Restore accepts 1..1000 deltas")
        changed, warnings = [], []
        for delta in deltas:
            ident = delta["id"]
            target = delta["before"] if undo else delta["after"]
            source = GraphSource.model_validate(target) if target is not None else None
            if source is not None and source.id != ident:
                raise ValueError("Source identity differs")
            try:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.store._check_owner(db, ident)
                    try:
                        current = self.store._read(db, ident)
                    except KeyError:
                        current = None
                    if current is None:
                        deletion = db.execute(
                            "SELECT * FROM graph_deletions WHERE trajectory_id=?", (ident,)
                        ).fetchone()
                        if deletion is None or (deletion["revision"], deletion["commit_id"]) != (
                            delta["expected_revision"],
                            delta["expected_commit"],
                        ):
                            raise OwnershipConflict("Deletion changed")
                        old = self.store._read(db, ident, deletion["revision"])
                    else:
                        if (current.revision, current.commit_id) != (
                            delta["expected_revision"],
                            delta["expected_commit"],
                        ):
                            raise OwnershipConflict("Source changed since history entry")
                        old = current
                    updated = deepcopy(delta)
                    if source is None:
                        if current is None:
                            raise ValueError("Source already deleted")
                        updated.update(
                            expected_revision=current.revision,
                            expected_commit=self._delete(db, current),
                        )
                    else:
                        result = self.store._commit(
                            db, source, old, {"history": "undo" if undo else "redo"}
                        )
                        db.execute("DELETE FROM graph_deletions WHERE trajectory_id=?", (ident,))
                        updated.update(
                            expected_revision=result.revision, expected_commit=result.commit_id
                        )
                    changed.append(updated)
            except (OwnershipConflict, RevisionConflict, KeyError) as error:
                warnings.append(dict(id=ident, warning=str(error)))
        return dict(deltas=changed, warnings=warnings)
