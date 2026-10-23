"""Single-owner generation loop with bounded retries and atomic checkpoints."""

import asyncio
import copy
from collections import deque
from datetime import UTC, datetime

from .changes import SQLiteHistory
from .llm import SampleBuilder


class Runner:

    def __init__(self, state, compiler, save_path, client=None, executor=None):
        self.state, self.compiler, self.save_path = state, compiler, save_path
        self.client = client
        self.executor = executor
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
        if self.job and not self.job.done():
            raise ValueError("Generation is already running")
        config = copy.deepcopy(self.state["config"])
        if validate is not None:
            config["automatic"]["validate"] = bool(validate)
        seed_revision = self.state["revision"]
        seed = (
            copy.deepcopy(self.state["samples"][seed_index])
            if seed_index is not None
            else None
        )
        self.status.update(
            running=True,
            paused=False,
            accepted=0,
            attempts=0,
            failures=0,
            last_error=None,
        )
        self.resume_event.set()
        self.job = asyncio.create_task(
            self.run(config, single, task_ids, seed, seed_index, seed_revision)
        )

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

    async def run(self, config, single, task_ids, seed=None, seed_index=None, seed_revision=None):
        options = config.get("automatic", {})
        max_samples = 1 if single else options.get("max_samples", 10)
        max_attempts = options.get("max_attempts", 30)
        dirty = 0
        consecutive = 0
        try:
            while (not max_samples or self.status["accepted"] < max_samples) and (
                not max_attempts or self.status["attempts"] < max_attempts
            ):
                await self.resume_event.wait()
                self.status["attempts"] += 1
                feedback = ""
                accepted = None
                for retry in range(options.get("regenerations", 2) + 1):
                    await self.resume_event.wait()
                    builder = SampleBuilder(
                        self.compiler, config, self.client, self.log, self.executor
                    )
                    try:
                        sample = await builder.generate(feedback, task_ids, seed)
                        if options.get("validate", True):
                            report = await builder.validators(sample)
                            if not report["valid"]:
                                raise ValueError(
                                    "Validator rejected sample: "
                                    + "; ".join(
                                        s
                                        for r in report["reports"]
                                        for s in r["feedback"]
                                    )
                                )
                            sample.setdefault("metadata", {})["validation"] = report
                        accepted = sample
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        # Keep API credentials out of retained logs and feedback.
                        feedback = str(e)[:4000]
                        for ep in config["endpoints"]:
                            secret = ep.get("api_key")
                            if secret:
                                feedback = feedback.replace(secret, "[redacted]")
                        self.status["last_error"] = feedback
                        self.log(
                            f"Attempt {self.status['attempts']}, regeneration {retry}: {feedback}"
                        )
                if accepted is not None:
                    updated = self.state["samples"].copy()
                    if seed_index is not None:
                        updated.revision = seed_revision
                    if seed_index is None:
                        updated.append(accepted)
                    else:
                        updated[seed_index] = accepted
                    self.history.commit(
                        updated, f"Generate trajectory: {accepted['id']}"
                    )
                    self.status["accepted"] += 1
                    consecutive = 0
                    dirty += 1
                    self.log(f"Accepted {accepted['id']}")
                    if dirty >= options.get("autosave_every", 5):
                        await self.save()
                        dirty = 0
                else:
                    consecutive += 1
                    self.status["failures"] += 1
                    action = options.get("failure_action", "stop")
                    if (
                        consecutive >= options.get("max_consecutive_failures", 3)
                        or action == "stop"
                    ):
                        self.log("Stopped after generation failure threshold")
                        break
                    if action == "pause":
                        self.pause()
                if max_samples and self.status["accepted"] >= max_samples:
                    break
                await asyncio.sleep(options.get("delay_seconds", 1))
        except asyncio.CancelledError:
            self.log("Generation stopped; current incomplete sample discarded")
            raise
        except Exception as e:
            self.status["last_error"] = str(e)
            self.log(f"Runner stopped: {e}")
        finally:
            if dirty:
                try:
                    await self.save()
                except Exception as e:
                    self.status["last_error"] = f"Checkpoint failed: {e}"
                    self.log(self.status["last_error"])
            self.status.update(running=False, paused=False)
