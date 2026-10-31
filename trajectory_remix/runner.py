"""Single-owner generation loop with bounded retries and atomic checkpoints."""

import asyncio
from collections import deque
from datetime import UTC, datetime

from .changes import SQLiteHistory


class Runner:
    def __init__(self, state, compiler, save_path, client=None):
        self.state, self.compiler, self.save_path = state, compiler, save_path
        self.client = client
        self.history = SQLiteHistory(state)
        self.job = None
        self.save_lock = asyncio.Lock()
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.status = {
            "running": False,
            "paused": False,
            "accepted": 0,
            "attempts": 0,
            "failures": 0,
            "last_error": None,
            "last_saved": None,
            "last_saved_revision": None,
        }
        self.logs = deque(maxlen=250)

    def log(self, message):
        self.logs.append({"time": datetime.now(UTC).isoformat(), "message": message})

    async def save(self):
        async with self.save_lock:
            # Source edits are durable at commit. A checkpoint never rewrites
            # trajectories or copies the dataset into memory.
            revision = self.state["revision"]
            self.status["last_saved"] = datetime.now(UTC).isoformat()
            self.status["last_saved_revision"] = revision
            self.log(f"Checkpointed {len(self.state['samples'])} samples in {self.save_path.name}")
            return revision

    def start(self, single=False, task_ids=None, seed_index=None, validate=None):
        if getattr(self, "tokenizer_migration", None) and self.tokenizer_migration.running:
            raise ValueError("Wait for tokenizer migration to finish")
        raise ValueError("Task-based generation is retired; use a content-first scenario")

    async def stop(self):
        if self.job and not self.job.done():
            self.job.cancel()
            try:
                await self.job
            except asyncio.CancelledError:
                pass
        self.status.update(running=False, paused=False)

    def pause(self):
        self.resume_event.clear()
        self.status["paused"] = True

    def resume(self):
        self.resume_event.set()
        self.status["paused"] = False
