"""One session-owned numeric cache job, independent of any MCP/HTTP request."""

import asyncio
import threading
import time
import uuid

from . import metrics


class MetricsRefresh:
    def __init__(self, store):
        self.store, self.task = store, None
        self.cancel_event = threading.Event()
        self.started, self.finished = None, None
        self.status = {"status": "idle", "job_id": None, "processed": 0, "total": 0}

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def snapshot(self, job_id=None):
        if job_id is not None and job_id != self.status["job_id"]:
            raise ValueError("Metrics job is no longer current; poll without job_id to find the current job")
        return {**self.status, "elapsed_seconds": round((self.finished or time.monotonic()) - self.started, 2) if self.started else 0}

    def start(self, compiler, limit=None):
        # Retrying after a transport timeout joins the same job, never queues a
        # duplicate worker behind a lock. Dataset work continues after disconnect.
        if self.running:
            return self.snapshot()
        remaining = metrics.pending(self.store, compiler)
        total = min(remaining, limit) if limit is not None else remaining
        self.cancel_event = threading.Event()
        self.started = time.monotonic()
        self.finished = None if total else self.started
        self.status = {"status": "running" if total else "completed", "job_id": uuid.uuid4().hex,
                       "processed": 0, "total": total, "remaining": remaining, "reused": 0,
                       "revision": self.store.info()[0], "current_sample_id": None, "current_uuid": None}
        if total:
            self.task = asyncio.create_task(self._run(compiler, total))
        return self.snapshot()

    async def _run(self, compiler, total):
        loop = asyncio.get_running_loop()

        def progress(processed, sample_id, ident):
            loop.call_soon_threadsafe(self.status.update, {"processed": processed,
                                     "current_sample_id": sample_id, "current_uuid": ident})
        try:
            result = await asyncio.to_thread(metrics.refresh, self.store, compiler, total,
                                            cancel=self.cancel_event, progress=progress)
            self.status.update(result, status="cancelled" if self.cancel_event.is_set() else "completed",
                               current_sample_id=None, current_uuid=None)
        except Exception as error:
            self.status.update(status="failed", error=str(error),
                               failed_sample_id=self.status.get("current_sample_id"),
                               failed_uuid=self.status.get("current_uuid"),
                               current_sample_id=None, current_uuid=None)
        finally:
            self.finished = time.monotonic()

    def cancel(self, job_id=None):
        self.snapshot(job_id)
        if self.running:
            self.cancel_event.set()
            self.status["status"] = "stopping"
        return self.snapshot()

    async def close(self):
        if self.running:
            self.cancel_event.set()
            await asyncio.shield(self.task)
