"""Durable references to PR #72's leased temporary artifact service."""

from __future__ import annotations


class JobArtifacts:
    def __init__(self, jobs, artifacts):
        self.jobs, self.store, self.artifacts = jobs, jobs.store, artifacts

    def start_export(self, options, *, profile=None, active_limit=8, seed=0):
        # Snapshot references/settings in one read/write transaction. Immutable
        # revisions remain readable if a source is subsequently edited/deleted.
        from dataclasses import asdict

        from .export import ExportOptions

        try:
            options = ExportOptions(**options)
        except TypeError as error:
            raise ValueError("Unknown or invalid export options") from error
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = self.store._settings(db)
            if options.compiled and profile not in settings.settings.tokenizer_profiles:
                raise ValueError("Compiled export requires an existing profile")
            refs = [
                dict(r)
                for r in db.execute("SELECT id,revision FROM graph_trajectories ORDER BY id")
            ]
            return self.jobs._create(
                db,
                "export",
                dict(
                    options=asdict(options),
                    profile=profile,
                    active_limit=active_limit,
                    seed=seed,
                    sources=refs,
                    settings_revision=settings.revision,
                ),
                len(refs),
            )

    def current(self, job, epoch):
        j = self.jobs.inspect(job)
        return j["state"] == "running" and j["epoch"] == epoch

    def build_export(self, job):
        from .export import ExportOptions

        j = self.jobs.inspect(job)
        if j["kind"] != "export" or j["state"] != "running":
            raise ValueError("Export job is not running")
        c, epoch = j["config"], j["epoch"]
        settings = self.store.settings(c["settings_revision"]).settings
        records = [self.store.read(r["id"], r["revision"]) for r in c["sources"]]
        compiler = None
        if c["options"]["compiled"]:
            from .compiler import GraphCompiler

            compiler = GraphCompiler(
                settings.tokenizer_profiles[c["profile"]],
                settings=settings,
                active_limit=c["active_limit"],
                seed=c["seed"],
            )
        artifact, manifest = self.artifacts.build(
            records,
            settings,
            compiler=compiler,
            options=ExportOptions(**c["options"]),
            cancelled=lambda: not self.current(job, epoch),
        )
        self._publish(job, epoch, artifact)
        return manifest

    def export_logs(self, job):
        """Snapshot retained text/cursor. Download job is independent of its source."""
        with self.store.connect() as db:
            db.execute("BEGIN")  # WAL read snapshot; never a writer during file I/O.
            self.jobs._job(db, job)
            state = db.execute("SELECT * FROM graph_log_cursors WHERE job_id=?", (job,)).fetchone()
            download = self.jobs.create_job(
                "log_export",
                dict(source_job=job, through=state["last"], truncated=bool(state["lost_through"])),
                total=1,
            )
            text = (
                (r[0] + "\n").encode()
                for r in db.execute(
                    "SELECT text FROM graph_job_logs WHERE job_id=? AND sequence<=? ORDER BY sequence",
                    (job, state["last"]),
                )
            )
            artifact = self.artifacts.publish_files(
                {"logs.txt": text},
                metadata=dict(
                    source_job=job, through=state["last"], truncated=bool(state["lost_through"])
                ),
            )
        self._publish(download, 1, artifact)
        return download

    def _publish(self, job, epoch, artifact):
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self.jobs._fence(db, job, epoch)
                db.execute(
                    "UPDATE graph_jobs SET artifact=?,state='completed',progress=total WHERE id=?",
                    (artifact, job),
                )
                self.jobs._notify(db, job)
        except BaseException:
            self.artifacts.discard(artifact)
            raise

    def lease(self, job, name):
        j = self.jobs.inspect(job)
        if j["state"] != "completed" or not j["artifact"]:
            raise FileNotFoundError("No completed artifact")
        return self.artifacts.lease(j["artifact"], name)
