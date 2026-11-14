"""REST adapter for graph jobs. Mount on the graph studio app, not replay state."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from .bulk import BulkService
from .jobs import Fenced, JobService, OwnershipConflict
from .models import DTO, JsonValue
from .storage import GraphStore, RevisionConflict


class RunInput(DTO):
    config: dict[str, JsonValue]
    proposals: list[dict[str, JsonValue]] = Field(min_length=1)


class BulkInput(DTO):
    query: dict[str, JsonValue] = Field(default_factory=dict)
    action: Literal['stage', 'split', 'metrics', 'delete', 'review_repair']
    config: dict[str, JsonValue] = Field(default_factory=dict)


class HistoryDelta(DTO):
    id: str = Field(min_length=1)
    before: dict[str, JsonValue] | None
    after: dict[str, JsonValue] | None
    expected_revision: int = Field(ge=1)
    expected_commit: str = Field(min_length=1)


class HistoryInput(DTO):
    deltas: list[HistoryDelta] = Field(min_length=1, max_length=1000)
    undo: bool = True


class ExportInput(DTO):
    options: dict[str, JsonValue] = Field(default_factory=dict)
    profile: str | None = None
    active_limit: int = Field(default=8, ge=1)
    seed: int = 0


class JobWorker:
    """Host-owned tasks survive HTTP disconnects. No built-in model driver.

    Handoff 06 registers async job handlers and schedules through JobService's
    endpoint claims; it must not call a provider directly from an HTTP handler.
    """
    def __init__(self, jobs, bulk, artifacts, *, handlers=None, bulk_handlers=None):
        self.jobs, self.bulk, self.artifacts = jobs, bulk, artifacts
        self.handlers, self.bulk_handlers = handlers or {}, bulk_handlers or {}
        self.tasks = {}

    def kick(self, job):
        if job not in self.tasks or self.tasks[job].done():
            self.tasks[job] = asyncio.create_task(self._run(job))

    async def _run(self, job):
        j = self.jobs.inspect(job)
        try:
            if j['kind'] == 'bulk':
                while self.jobs.inspect(job)['state'] == 'running':
                    await asyncio.to_thread(self.bulk.advance, job, self.bulk_handlers)
                    await asyncio.sleep(0)
            elif j['kind'] == 'export':
                await asyncio.to_thread(self.artifacts.build_export, job)
            elif j['kind'] in self.handlers:
                await self.handlers[j['kind']](self.jobs, job)
            else:
                self.jobs.control(job, 'pause')
                self.jobs.append_log(job, 'No driver registered; manual Resume after driver installation.')
        except (Fenced, asyncio.CancelledError):
            pass
        except Exception:
            # Keep provider/driver exception bodies out of durable logs.
            if self.jobs.inspect(job)['state'] == 'running':
                self.jobs.control(job, 'pause')
                self.jobs.append_log(job, 'Operation interrupted; completed commits retained. Manual Resume required.')
        finally:
            # Resume can arrive while a fenced thread is settling. Start the new
            # epoch only after that thread has returned, never run both epochs.
            latest = self.jobs.inspect(job)
            if latest['state'] == 'running' and latest['epoch'] != j['epoch']:
                self.tasks.pop(job, None)
                self.kick(job)

    async def close(self):
        for job, task in list(self.tasks.items()):
            if not task.done() and self.jobs.inspect(job)['state'] == 'running':
                self.jobs.control(job, 'pause')
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def job_router(jobs, bulk, artifacts, worker):
    router = APIRouter(prefix='/api/v1')

    def accepted(job):
        return JSONResponse(dict(job_id=job, status_url=f'/api/v1/jobs/{job}'), status_code=202)

    @router.get('/jobs')
    def listing(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return jobs.listing(limit=limit, offset=offset)

    @router.get('/jobs/{job}')
    def inspect(job: str):
        return jobs.inspect(job)

    @router.post('/jobs/{job}/{action}')
    async def control(job: str, action: Literal['pause', 'resume', 'cancel']):
        result = jobs.control(job, action)
        if action == 'resume':
            worker.kick(job)
        return result

    @router.post('/jobs/{job}/detach/{trajectory}')
    def detach(job: str, trajectory: str):
        jobs.detach(job, trajectory)
        return jobs.inspect(job)

    @router.get('/jobs/{job}/attempts')
    def attempts(job: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return jobs.attempts(job, limit=limit, offset=offset)

    @router.post('/runs')
    async def run(body: RunInput):
        job = jobs.create_run(body.config, body.proposals)
        worker.kick(job)
        return accepted(job)

    @router.post('/bulk')
    async def start_bulk(body: BulkInput):
        if body.action in {'metrics', 'review_repair'} and body.action not in worker.bulk_handlers:
            raise ValueError(f'{body.action} driver is not installed')
        job = bulk.start(body.query, body.action, body.config)
        worker.kick(job)
        return accepted(job)

    @router.get('/jobs/{job}/results')
    def results(job: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return bulk.results(job, limit=limit, offset=offset)

    @router.get('/jobs/{job}/history')
    def history(job: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return bulk.history(job, limit=limit, offset=offset)

    @router.post('/bulk/history')
    def restore(body: HistoryInput):
        return bulk.restore([d.model_dump(mode='json') for d in body.deltas], undo=body.undo)

    @router.get('/jobs/{job}/logs')
    def logs(job: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        return jobs.logs(job, after=after, limit=limit)

    @router.delete('/logs')
    def clear_logs(job: str | None = None):
        jobs.clear_logs(job)
        return dict(cleared=True)

    @router.get('/logs/usage')
    def log_usage():
        return jobs.log_usage()

    @router.post('/logs/{job}/export')
    async def export_logs(job: str):
        return accepted(await asyncio.to_thread(artifacts.export_logs, job))

    @router.post('/exports')
    async def export(body: ExportInput):
        job = artifacts.start_export(body.options, profile=body.profile, active_limit=body.active_limit, seed=body.seed)
        worker.kick(job)
        return accepted(job)

    @router.get('/jobs/{job}/artifacts/{name}')
    def download(job: str, name: str):
        # Acquire before headers so expiry/unavailable files return 404.
        lease = artifacts.lease(job, name)
        stream = lease.__enter__()

        def body():
            try:
                while chunk := stream.read(64 * 1024):
                    yield chunk
            finally:
                lease.__exit__(None, None, None)
        from starlette.background import BackgroundTask
        return StreamingResponse(body(), media_type='application/octet-stream',
                                 background=BackgroundTask(lease.__exit__, None, None, None))

    @router.post('/endpoints/{endpoint}/resume')
    async def resume_endpoint(endpoint: str):
        jobs.resume_endpoint(endpoint)
        for j in jobs.listing(limit=1000):
            if j['state'] == 'running':
                worker.kick(j['id'])
        return dict(endpoint=endpoint, paused=False)

    @router.get('/changes')
    def changes(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        return jobs.changes(after, limit=limit)

    return router


def create_job_app(path, *, artifact_root='/tmp/trajectory-remix-exports', handlers=None, bulk_handlers=None):
    """Headless graph service factory, separate from the old replay dashboard.

    Localhost host/auth policy belongs to the mounting studio transport. This
    factory supplies no credentials or provider access. Mount job_router in the
    studio's authenticated app when handoff 03 supplies that transport.
    """
    from .export import ArtifactService
    from .job_artifacts import JobArtifacts
    jobs = JobService(GraphStore(path))
    bulk = BulkService(jobs)
    artifacts = JobArtifacts(jobs, ArtifactService(artifact_root))
    worker = JobWorker(jobs, bulk, artifacts, handlers=handlers, bulk_handlers=bulk_handlers)

    @asynccontextmanager
    async def lifespan(app):
        async def expiry():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(artifacts.artifacts.expire)
        task = asyncio.create_task(expiry())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await worker.close()

    app = FastAPI(title='Graph jobs', lifespan=lifespan)
    app.state.jobs, app.state.bulk, app.state.worker = jobs, bulk, worker
    app.state.artifacts = artifacts

    @app.exception_handler(OwnershipConflict)
    @app.exception_handler(RevisionConflict)
    async def conflict(request, error):
        return JSONResponse(dict(code=error.code, message=str(error),
            **{key: getattr(error, key) for key in ('entity_id', 'expected_revision', 'current_revision', 'commit_id') if hasattr(error, key)}), status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse(dict(code='invalid_operation', message=str(error)), status_code=422)

    @app.exception_handler(KeyError)
    @app.exception_handler(FileNotFoundError)
    async def missing(request, error):
        return JSONResponse(dict(code='not_found'), status_code=404)

    app.include_router(job_router(jobs, bulk, artifacts, worker))
    return app
