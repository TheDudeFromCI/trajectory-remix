"""Manual graph application queries shared by REST and agent tasks.

No tokenizer/compiler import occurs until an explicit tick-history request.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from threading import Lock

from fastapi.encoders import jsonable_encoder

from .bulk import BulkService
from .commands import GraphCommands, GraphResult
from .diagnostic_policy import choices
from .jobs import page
from .models import GraphSource, ProjectSettings, digest
from .validation import diagnose


def result(value):
    record = value.record
    return dict(
        source=record.source.to_dict(),
        revision=record.revision,
        commit_id=record.commit_id,
        intrinsic_hash=record.intrinsic_hash,
        intrinsic_revision=record.intrinsic_revision,
        metadata_revision=record.metadata_revision,
        settings_revision=record.settings_revision,
        diagnostics_settings_revision=record.settings_revision,
        diagnostics=jsonable_encoder(value.diagnostics),
        has_errors=value.has_errors,
    )


class Studio:
    def __init__(self, jobs, bulk, artifacts, worker):
        self.jobs, self.store = jobs, jobs.store
        self.bulk, self.artifacts, self.worker = bulk, artifacts, worker
        self.commands = GraphCommands(self.store)
        self.previews, self.preview_bytes = OrderedDict(), 0
        self.preview_lock = Lock()

    def listing(self, query=None, *, limit=50, offset=0):
        page(limit, offset)
        where, args = BulkService._where(query or {})
        with self.store.connect() as db:
            db.execute("BEGIN")
            join = """FROM graph_trajectories t JOIN graph_trajectory_revisions r
                ON r.trajectory_id=t.id AND r.revision=t.revision"""
            total = db.execute(f"SELECT count(*) {join} WHERE {where}", args).fetchone()[0]
            rows = db.execute(
                f"""SELECT t.id,t.revision,r.commit_id,r.intrinsic_hash,
                json_extract(r.source,'$.metadata.stage') AS stage,
                json_extract(r.source,'$.metadata.split') AS split,
                (SELECT job_id FROM graph_owners WHERE trajectory_id=t.id) AS owner
                {join} WHERE {where} ORDER BY t.id LIMIT ? OFFSET ?""",
                [*args, limit, offset],
            )
            return dict(items=[dict(r) for r in rows], total=total, limit=limit, offset=offset)

    def settings(self):
        r = self.store.settings()
        return dict(
            settings=r.settings.model_dump(mode="json"),
            revision=r.revision,
            commit_id=r.commit_id,
            validation_warnings=choices(),
        )

    def present(self, value):
        from .reviews import stale

        return result(value) | dict(
            review_status=stale(
                value.record.source, self.store.settings(value.record.settings_revision).settings
            )
        )

    def update_settings(self, settings, expected_revision):
        self.store.update_settings(ProjectSettings.model_validate(settings), expected_revision)
        self.jobs.configure_endpoints()
        return self.settings()

    def read(self, ident, revision=None):
        from .reviews import stale

        with self.store.connect() as db:
            db.execute("BEGIN")
            record = self.store._read(db, ident, revision)
            owner = self.store._owner(db, ident)
            snapshot = self.store._settings(
                db, record.settings_revision if revision or owner else None
            )
        source, settings = record.source, snapshot.settings
        return result(GraphResult(record, diagnose(source, settings))) | dict(
            owner=owner,
            review_status=stale(source, settings),
            diagnostics_settings_revision=snapshot.revision,
        )

    def graph(self, ident):
        value = self.read(ident)
        source = value["source"]
        # Stable dependency order for scrubbing, with broken/cyclic drafts still editable.
        pending, order = set(source["events"]), []
        while pending:
            ready = sorted(e for e in pending if not (set(source["events"][e]["after"]) & pending))
            if not ready:
                ready = sorted(pending)
            order.extend(ready)
            pending.difference_update(ready)
        return dict(
            order=order,
            source_hash=value["intrinsic_hash"],
            revision=value["revision"],
            diagnostics=value["diagnostics"],
        )

    def preview(
        self, ident, revision, profile, *, active_limit=8, seed=0, max_frames=1000, source=None
    ):
        from .compiler import VERSION, GraphCompiler

        record = self.store.read(ident, revision)
        draft = record.source if source is None else GraphSource.model_validate(source)
        if draft.id != ident:
            raise ValueError("Preview source identity must match trajectory")
        settings_record = self.store.settings()
        settings = settings_record.settings
        if profile not in settings.tokenizer_profiles:
            raise ValueError("Select an existing tokenizer profile in Preferences")
        selected = settings.tokenizer_profiles[profile]
        key = digest(
            [
                VERSION,
                draft.intrinsic_hash,
                selected.fingerprint,
                settings_record.commit_id,
                active_limit,
                seed,
                max_frames,
            ]
        )
        with self.preview_lock:
            encoded = self.previews.get(key)
            if encoded:
                self.previews.move_to_end(key)
                return json.loads(encoded) | dict(revision=revision)
        compiler = GraphCompiler(selected, settings=settings, active_limit=active_limit, seed=seed)
        preview = compiler.preview(draft, max_frames=max_frames)
        data = jsonable_encoder(preview) | dict(
            profile=profile,
            profile_fingerprint=selected.fingerprint,
            settings_revision=settings_record.revision,
            cache_key=key,
        )
        # Decode ordinary tokens for the retained window renderer. Controls stay logical labels.
        for frame in data["frames"]:
            for emission in frame["inputs"] + frame["outputs"]:
                for token in emission["tokens"]:
                    token["text"] = (
                        compiler.tokenizer.decode([token["id"]], skip_special_tokens=False)
                        if token["id"] is not None and not token["control"]
                        else ""
                    )
        encoded = json.dumps(data)
        size = len(encoded.encode())
        # Finite byte and entry budgets; preview never writes source or artifacts.
        if size <= 8 * 1024 * 1024:
            with self.preview_lock:
                previous = self.previews.pop(key, None)
                if previous:
                    self.preview_bytes -= len(previous.encode())
                self.previews[key] = encoded
                self.preview_bytes += size
                while len(self.previews) > 16 or self.preview_bytes > 8 * 1024 * 1024:
                    _, old = self.previews.popitem(last=False)
                    self.preview_bytes -= len(old.encode())
        return data | dict(revision=revision)

    def delete(self, ident, revision):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.store._read(db, ident)
            self.store._check_revision(ident, revision, record)
            self.store._check_owner(db, ident)
            commit = self.bulk._delete(db, record)
            return dict(
                id=ident,
                before=record.source.to_dict(),
                after=None,
                expected_revision=record.revision,
                expected_commit=commit,
            )

    def start_import(self, text):
        # Freeze a bounded upload once; resume advances the committed per-line cursor.
        if len(text.encode()) > 40_000_000:
            raise ValueError("Import exceeds 40 MB; split the upload")
        try:
            parsed = json.loads(text)
        except ValueError:
            lines = text.splitlines()
        else:
            lines = [json.dumps(v) for v in (parsed if isinstance(parsed, list) else [parsed])]
        lines = [line for line in lines if line.strip()]
        if not lines:
            raise ValueError("Import is empty")
        job = self.jobs.create_job("import", dict(lines=lines), total=len(lines))
        self.worker.kick(job)
        return job

    async def import_job(self, jobs, job):
        current = jobs.inspect(job)
        epoch = current["epoch"]
        for index in range(current["progress"], current["total"]):
            line = current["config"]["lines"][index]
            warning = None
            try:
                source = GraphSource.model_validate_json(line)
            except ValueError:
                source, warning = None, f"Item {index + 1}: invalid graph JSON; skipped."
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = jobs._fence(db, job, epoch)
                if row["progress"] != index:
                    raise ValueError("Import cursor changed")
                if source is not None:
                    exists = db.execute(
                        "SELECT 1 FROM graph_trajectory_revisions WHERE trajectory_id=?",
                        (source.id,),
                    ).fetchone()
                    if exists:
                        warning = f"Item {index + 1}: identity already exists; skipped."
                    else:
                        self.store._commit(db, source, None, dict(import_job=job, item=index + 1))
                db.execute("UPDATE graph_jobs SET progress=progress+1 WHERE id=?", (job,))
                jobs._notify(db, job)
            if warning:
                jobs.append_log(job, warning, level="warning")
            await asyncio.sleep(0)
        jobs.finish(job, epoch)
