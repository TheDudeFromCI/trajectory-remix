"""Single-sample YAML execution with isolated drafts and streamed, persisted progress."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import uuid
from collections import deque
from datetime import UTC, datetime

import jsonschema

from .content_first import assemble_streams, summarize_requests, validate_content, public_tool_contracts
from .trajectory_review import build_checklist
from .llm import SampleBuilder
from .model_session import ModelSession, ProviderFailure, EvaluationFailure
from .scenarios import Flow, Scenario, check_scenario_endpoints, resolve, sample_tags, canonical_scenario

NOVELTY = {
    "type": "object",
    "properties": {
        "similarity": {"type": "number", "minimum": 0, "maximum": 1},
        "feedback": {"type": "string"},
    },
    "required": ["similarity", "feedback"],
    "additionalProperties": False,
}


class Rejected(ValueError):
    pass


class ScenarioRunner(ModelSession):
    def __init__(self, legacy, registry, client=None):
        self.legacy, self.registry, self.client = legacy, registry, client
        self.state, self.compiler = legacy.state, legacy.compiler
        self.history = legacy.history
        self.job = None
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.logs = deque()
        self.status = {
            "running": False,
            "paused": False,
            "pause_requested": False,
            "phase": "idle",
            "completed": 0,
            "total": 0,
            "active": [],
            "draft_id": None,
            "accepted": 0,
            "attempts": 0,
        }
        with self.state.store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)"
            )
            row = db.execute(
                "SELECT snapshot FROM generation_jobs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        self.snapshot = json.loads(row[0]) if row else None
        if self.snapshot:
            self.status.update(self.snapshot["status"])
            self.logs.extend(self.snapshot["logs"])
            if self.status["running"]:
                self.status.update(
                    running=False,
                    paused=False,
                    pause_requested=False,
                    phase="interrupted",
                    active=[],
                )
                self.snapshot["status"] = self.status
                for request in self.snapshot.get('content_requests', []):
                    if request.get('state') == 'running':
                        request['state'] = 'interrupted'
                        request['finished_at'] = request['started_at'] + request.get('metrics', {}).get('generation_seconds', 0)
                self.persist()

        from .generation_resume import GenerationResumeQueue
        self.resume_queue = GenerationResumeQueue(self)

    @property
    def content_first(self):
        return hasattr(self, 'scenario') and self.scenario.flow.generation_mode == 'content-first'

    def elapsed_seconds(self):
        if hasattr(self, '_attempt_started'):
            return self._attempt_elapsed_base + time.monotonic() - self._attempt_started
        return self.snapshot.get('elapsed_seconds', 0)

    def generation_record(self):
        requests = self.snapshot.get('content_requests', [])
        elapsed = self.elapsed_seconds()
        return {
            'job': self.snapshot['id'],
            'requirements': {'flow': self.snapshot['flow'], 'prompts': self.snapshot['prompts'],
                             'context': copy.deepcopy({k: v for k, v in self.snapshot['context'].items() if k != 'steps'})},
            'requests': copy.deepcopy(requests),
            'failures': copy.deepcopy(self.snapshot.get('failed_proposals', [])),
            'recoveries': list(self.snapshot.get('recovery_history', [])),
            'metrics': summarize_requests(requests, elapsed, self.status['phase']),
        }

    def persist(self):
        if self.snapshot is None:
            return
        self.snapshot['elapsed_seconds'] = self.elapsed_seconds()
        self.snapshot.update(status=copy.deepcopy(self.status), logs=list(self.logs))
        if hasattr(self, "builder"):
            self.snapshot["checkpoint"] = {
                "sample": self.builder.sample,
                "next_tick": self.builder.next_tick,
                "serial": self.builder.serial,
                "expected_draft": self.expected_draft,
                "state_override": getattr(self, "state_override", None),
                "first_prompts": getattr(self, "first_prompts", {}),
                "first_prompt_paths": getattr(self, "first_prompt_paths", {}),
            }
        with self.state.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO generation_jobs VALUES (?,?)",
                (self.snapshot["id"], self.clean(self.snapshot)),
            )

    def log(self, message, level="info", step=None):
        self.logs.append(
            {
                "time": datetime.now(UTC).isoformat(),
                "message": self.clean(message),
                "level": level,
                "step": step,
            }
        )
        self.persist()

    def start(
        self,
        name,
        max_parallel=None,
        *,
        _retry=False,
        _schedule=True,
        tags=None,
        batch_size=1,
    ):
        validator = getattr(self.legacy, "validation_runner", None)
        if validator and validator.status.get("running"):
            raise ValueError("Finish or cancel trajectory validation before generation")
        if getattr(self.legacy, "tokenizer_migration", None) and self.legacy.tokenizer_migration.running:
            raise ValueError("Wait for tokenizer migration to finish")
        if not _retry and (self.status["running"] or self.legacy.status["running"]):
            raise ValueError("Generation is already running")
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be an integer between 1 and 1000")
        if not _retry and name not in self.registry.scenarios:
            name = canonical_scenario(name) if name else next(iter(self.registry.listing()), {}).get("name")
        if not _retry and name not in self.registry.scenarios:
            raise ValueError("Unknown scenario; start Remix with --scenarios-dir")
        if max_parallel is None:
            max_parallel = (
                self.state["config"].get("single_generation", {}).get("max_parallel", 1)
            )
        if type(max_parallel) is not int or not 1 <= max_parallel <= 128:
            raise ValueError("max_parallel must be an integer between 1 and 128")
        if not _retry:
            self.scenario = self.registry.scenarios[name]
            self.manual_tags = tags
        pools = {
            "scenario": self.scenario.flow.tags,
            **{
                role: profile.tags for role, profile in self.scenario.flow.roles.items()
            },
        }
        chosen = {}
        if self.manual_tags is not None:
            if not isinstance(self.manual_tags, dict) or set(self.manual_tags) != set(
                pools
            ):
                raise ValueError("Manual tags must specify every scenario/role scope")
            for scope, dimensions in pools.items():
                selected = self.manual_tags[scope]
                if not isinstance(selected, dict) or set(selected) != set(dimensions):
                    raise ValueError(
                        f"Manual tags must specify every {scope} dimension"
                    )
                chosen[scope] = {}
                for dimension, options in dimensions.items():
                    option = next(
                        (t for t in options if t.name == selected[dimension]), None
                    )
                    if option is None:
                        raise ValueError(f"Unknown tag for {scope}.{dimension}")
                    chosen[scope][dimension] = option.model_dump(exclude={"weight"})
        self.config = copy.deepcopy(self.state["config"])
        self.endpoints = {e["id"]: e for e in self.config["endpoints"]}

        try:
            check_scenario_endpoints(name, self.scenario.flow, self.config["endpoints"], content_first=self.content_first)
        except ValueError as error:
            self.log(str(error), "error")
            raise
        if not _retry:
            self.limit = asyncio.Semaphore(max_parallel)
        self.first_prompts = {}
        self.first_prompt_paths = {}
        self.status["metrics"] = {}
        if not _retry:
            self.status["batch"] = {
                "id": str(uuid.uuid4()),
                "target": batch_size,
                "completed": 0,
                "attempts": 0,
                "attempt_limit": batch_size
                * self.config.get("single_generation", {}).get("max_attempts", 3),
                "state": "running",
            }
            self.resume_event.set()
        self.status["batch"]["attempts"] += 1
        paused = not self.resume_event.is_set()
        self.status.update(
            running=True,
            paused=paused,
            pause_requested=paused,
            phase="generating",
            completed=0,
            total=0,
            active=[],
            draft_id=None,
            model_requests=0,
            completed_model_seconds=0,
            total_model_seconds=0,
            attempts=self.status["attempts"] + 1,
            scenario=name,
            recovery_id=None,
            activity=self.status.get("activity", "create") if _retry else "create",
            last_error=None,
        )
        roles = {
            role: {
                "instructions": profile.instructions,
                "tags": chosen[role]
                if self.manual_tags is not None
                else sample_tags(profile.tags),
            }
            for role, profile in self.scenario.flow.roles.items()
        }
        self.context = {
            "scenario": {
                "tags": chosen["scenario"]
                if self.manual_tags is not None
                else sample_tags(self.scenario.flow.tags)
            },
            "roles": roles,
            "steps": {},
            "tools": self.compiler.tools.schemas(),
            "tool_contracts": public_tool_contracts(self.compiler.tools.schemas()),
        }
        self.builder = SampleBuilder(self.compiler, self.config)
        self.builder.sample["metadata"].update(
            generation_mode=self.scenario.flow.generation_mode,
            origin="automation",
            validation_level=0,
            scenario=name,
            scenario_family=getattr(self.scenario, 'family', name),
            generation_batch=self.status["batch"]["id"],
            context_tags=self.context["scenario"]["tags"],
            roles=roles,
        )
        self.builder.add_stream(
            "system", self.scenario.flow.system_prompt, op="append", source="system"
        )
        self.builder.sample["metadata"]["tags"] = [
            self.scenario.flow.tag or name,
        ] + [tag["name"] for tag in self.context["scenario"]["tags"].values()] + [
            f"{role}:{tag['name']}"
            for role, profile in roles.items()
            for tag in profile["tags"].values()
        ]
        self.builder.sample["metadata"]["review_checklist"] = build_checklist(
            self.scenario
        ).model_dump()
        self.status["conditions"] = {
            "scenario": self.context["scenario"]["tags"],
            **{role: profile["tags"] for role, profile in roles.items()},
        }
        self.expected_draft = None
        self.state_override = None
        self.snapshot = {
            "id": str(uuid.uuid4()),
            "flow": self.scenario.flow.model_dump(),
            "prompts": self.scenario.prompts,
            "context": self.context,
            "steps": {},
            "status": self.status,
            "logs": [],
        }
        self.snapshot["tool_fingerprint"] = self.compiler.tools.fingerprint
        self.snapshot["tokenizer_fingerprint"] = self.compiler.fingerprint
        self.snapshot["max_parallel"] = max_parallel
        self.snapshot["manual_tags"] = self.manual_tags
        self.builder.sample["metadata"].update(
            generation_phase="planning", generation_job=self.snapshot["id"]
        )
        self.publish()
        self.log(f"Starting {name}; maximum concurrent LLM calls: {max_parallel}")
        if _schedule:
            self.job = asyncio.create_task(self.run(name, max_parallel))

    def assign_identifier(self, proposed):
        if self.snapshot.get("retain_identifier"):
            return
        words = re.findall(r"[a-z0-9]+", str(proposed).lower())[:4]
        if not words:
            raise ValueError("Idea identifier needs words")
        base = "-".join(words)
        current = self.builder.sample["id"]
        if current.rsplit("-", 1)[0] == base and current.rsplit("-", 1)[-1].isdigit():
            return
        suffix = 1
        while self.state.store.samples.index_of(f"{base}-{suffix}") is not None:
            suffix += 1
        ident = f"{base}-{suffix}"
        previous = self.builder.sample["id"]
        self.builder.sample["id"] = ident
        if self.expected_draft is not None:
            draft = copy.deepcopy(self.expected_draft)
            draft["id"] = ident
            samples = self.state["samples"].copy()
            index = self.state.store.samples.index_of(previous)
            samples[index] = draft
            self.history.commit(samples, f"Name generated trajectory: {ident}")
            self.expected_draft = draft
            self.status.update(draft_id=ident, draft_index=index)
        self.persist()

    def locked(self, sample_id):
        return self.status["running"] and sample_id == self.status.get("draft_id")

    def can_resume(self, sample_id):
        index = self.state.store.samples.index_of(sample_id) if sample_id else None
        if index is None:
            return False
        metadata = self.state["samples"][index].get("metadata", {})
        return (
            isinstance(metadata, dict)
            and metadata.get("generation_phase") == "planning"
            and bool(metadata.get("generation_job"))
        )

    def resume_saved(self, sample_id, *, confirm_edited=False, _schedule=True, force=False):
        validator = getattr(self.legacy, "validation_runner", None)
        if validator and validator.status.get("running"):
            raise ValueError("Finish or cancel trajectory validation before generation")
        if getattr(self.legacy, "tokenizer_migration", None) and self.legacy.tokenizer_migration.running:
            raise ValueError("Wait for tokenizer migration to finish")
        if self.status["running"] or self.legacy.status["running"]:
            raise ValueError("Generation already running")
        if not self.can_resume(sample_id):
            raise ValueError("Select a trajectory still in planning")
        sample = self.state["samples"][self.state.store.samples.index_of(sample_id)]
        with self.state.store.connect() as db:
            row = db.execute(
                "SELECT snapshot FROM generation_jobs WHERE id=?",
                (sample["metadata"]["generation_job"],),
            ).fetchone()
        if row is None:
            raise ValueError("Generation checkpoint not found")
        saved = json.loads(row[0])
        if not force and (
            saved.get("tool_fingerprint", self.compiler.tools.fingerprint)
            != self.compiler.tools.fingerprint
            or saved.get("tokenizer_fingerprint", self.compiler.fingerprint)
            != self.compiler.fingerprint
        ):
            raise ValueError(
                "Tokenizer or tools changed since checkpoint; refusing an inconsistent resume"
            )
        cp = saved["checkpoint"]
        if cp["sample"].get("uuid") != sample.get("uuid"):
            raise ValueError("Generation checkpoint belongs to another trajectory")
        if force:
            compiled = self.compiler.compile(cp["sample"])
            cp["next_tick"] = max(cp["next_tick"], compiled["max_tick"] + 1)
            saved["tool_fingerprint"] = self.compiler.tools.fingerprint
            saved["tokenizer_fingerprint"] = self.compiler.fingerprint
            cp["sample"].setdefault("metadata", {}).update(validation_level=0, generation_phase="planning")
            for key in ("evaluations", "approval"):
                cp["sample"]["metadata"].pop(key, None)
            cp["state_override"] = None
        if confirm_edited or force:
            cp["expected_draft"] = copy.deepcopy(sample)
            cp["sample"]["id"] = sample["id"]
        if sample != cp["expected_draft"] and not (confirm_edited or force):
            raise ValueError(
                "Trajectory edited since checkpoint; resume would overwrite edits"
            )
        self.snapshot = saved
        self.scenario = object.__new__(Scenario)
        self.scenario.flow = Flow.model_validate(self.snapshot["flow"])
        self.scenario.name = self.snapshot["status"].get(
            "scenario", sample["metadata"].get("scenario")
        )
        self.scenario.prompts = self.snapshot["prompts"]
        self.context = self.snapshot["context"]
        self.context["steps"] = {}
        self.config = copy.deepcopy(self.state["config"])
        self.endpoints = {e["id"]: e for e in self.config["endpoints"]}
        self.builder = SampleBuilder(self.compiler, self.config)
        self.builder.sample, self.builder.next_tick, self.builder.serial = (
            cp["sample"],
            cp["next_tick"],
            cp["serial"],
        )
        self.expected_draft, self.state_override = (
            cp["expected_draft"],
            cp.get("state_override"),
        )
        self.manual_tags = self.snapshot.get("manual_tags")
        self.first_prompts = cp.get("first_prompts", {})
        self.first_prompt_paths = cp.get("first_prompt_paths", {})
        cumulative = {
            key: max(self.status.get(key, 0), self.snapshot["status"].get(key, 0))
            for key in ("accepted", "attempts")
        }
        self.status.update(self.snapshot["status"])
        self.status.update(cumulative)
        self.status.update(
            running=True,
            phase="generating",
            activity="resume",
            paused=False,
            pause_requested=False,
            active=[],
            last_error=None,
            metrics={},
        )
        self.logs = deque(self.snapshot["logs"])
        self.limit = asyncio.Semaphore(self.snapshot.get("max_parallel", 1))
        self.resume_event.set()
        self.log("Resuming saved generation checkpoint; completed steps will be reused")
        if _schedule:
            self.job = asyncio.create_task(
                self.run(self.scenario.name, self.snapshot.get("max_parallel", 1))
            )

    async def gate(self):
        if not self.resume_event.is_set():
            self.status["paused"] = not bool(self.status["active"])
            self.persist()
        await self.resume_event.wait()

    def pause(self):
        self.resume_event.clear()
        self.status.update(pause_requested=True, paused=not bool(self.status["active"]))
        self.log("Pause requested; active LLM calls will finish", "warning")

    def resume(self):
        self.status.update(paused=False, pause_requested=False)
        self.resume_event.set()
        self.log("Resumed")

    async def stop(self):
        if self.job and not self.job.done():
            self.job.cancel()
            try:
                await self.job
            except asyncio.CancelledError:
                pass
        # Cancellation before a newly scheduled queue first runs has no async
        # finally block; still make its status durable and release the UI locks.
        if self.resume_queue.status['running'] and self.job and self.job.done():
            self.resume_queue.status.update(running=False, state='cancelled', current_id=None)
            self.resume_queue.persist()
            self.status.update(running=False, phase='cancelled', paused=False, pause_requested=False, active=[])
            self.persist()

        recovery_id = getattr(self, 'active_recovery_id', None)
        if recovery_id and self.job and self.job.done():
            self.legacy.generation_recovery.update(recovery_id, 'interrupted', 'Recovery cancelled before inference; checkpoint retained')
            self.active_recovery_id = None
            self.status.update(running=False, phase='cancelled', paused=False, pause_requested=False, active=[])
            self.persist()

    async def run(self, name, max_parallel):
        batch = self.status.setdefault(
            "batch",
            {
                "id": str(uuid.uuid4()),
                "target": 1,
                "completed": 0,
                "attempts": 1,
                "attempt_limit": self.config.get("single_generation", {}).get(
                    "max_attempts", 3
                ),
                "state": "running",
            },
        )
        batch["state"] = "running"
        try:
            while True:
                await self.gate()
                await self.run_attempt()
                if self.status["phase"] in ("api_paused", "evaluation_failed"):
                    batch["state"] = "paused"
                    return
                if self.status["phase"] == "complete":
                    batch["completed"] += 1
                    self.log(
                        f"Batch progress: {batch['completed']}/{batch['target']} completed; {batch['attempts']}/{batch['attempt_limit']} attempts",
                        "success",
                    )
                elif self.status["phase"] not in ("error", "rejected"):
                    return
                if batch["completed"] >= batch["target"]:
                    batch["state"] = "complete"
                    return
                if batch["attempts"] >= batch["attempt_limit"]:
                    batch["state"] = "exhausted"
                    self.log(
                        f"Attempt limit reached: {batch['completed']}/{batch['target']} trajectories completed",
                        "warning",
                    )
                    return
                self.status["running"] = True
                if self.status["phase"] in ("error", "rejected"):
                    self.log(
                        "Attempt failed; retrying with freshly sampled conditions",
                        "warning",
                    )
                # Publish the next planning checkpoint before yielding. A pause or
                # shutdown between samples must still leave a resumable trajectory.
                self.start(name, max_parallel, _retry=True, _schedule=False)
                await self.gate()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            batch["state"] = "cancelled"
            self.status.update(
                running=False, phase="cancelled", paused=False, pause_requested=False
            )
            self.log("Generation stopped; log retained", "warning")
            raise
        except Exception as error:  # noqa: BLE001 — retain a visible stopped batch on setup failure
            batch["state"] = "error"
            self.status.update(
                running=False, phase="error", last_error=self.clean(str(error))
            )
            self.log(self.status["last_error"], "error")
        finally:
            self.persist()

    async def run_attempt(self):
        self._attempt_elapsed_base = self.snapshot.get('elapsed_seconds', 0)
        self._attempt_started = time.monotonic()
        try:
            await self.execute(self.scenario.flow.steps, self.context)
            if not self.status["draft_id"]:
                raise Rejected("Scenario completed without publishing a draft")
            self.builder.sample["metadata"]["generation_phase"] = "complete"
            self.status["phase"] = "complete"
            self.publish()
            if not self.snapshot.get('creation_counted'):
                self.status["accepted"] += 1
                self.snapshot['creation_counted'] = True
            self.log("Scenario complete", "success")
        except asyncio.CancelledError:
            self.status.update(phase="cancelled")
            self.log(
                "Stopped; checkpoint and trajectory retained for resume", "warning"
            )
            raise
        except ProviderFailure as error:
            self.status.update(phase="api_paused", last_error=str(error))
            self.log(
                "Model API unavailable; generation paused without rejecting the attempt",
                "warning",
            )
        except EvaluationFailure as error:
            self.snapshot["validation_passed"] = False
            pending = copy.deepcopy(self.expected_draft)
            pending["metadata"].update(
                generation_phase="planning", validation_level=0,
                evaluation_error=self.clean(str(error)),
            )
            self.publish(pending)
            self.status.update(phase="evaluation_failed", last_error=self.clean(str(error)))
            self.log("Evaluation failed; batch paused with a resumable draft: " + self.clean(str(error)), "error")
        except Rejected as error:
            if self.status["draft_id"] and not self.content_first:
                rejected = copy.deepcopy(self.expected_draft)
                rejected["metadata"]["generation_phase"] = "rejected"
                rejected["metadata"]["validation_level"] = min(
                    -1, rejected["metadata"].get("validation_level", 0)
                )
                try:
                    self.publish(rejected)
                except ValueError:
                    self.status.update(
                        phase="error",
                        last_error="Draft changed externally; rejection was not applied",
                    )
                    self.log(self.status["last_error"], "error")
                    return
            self.status.update(phase="rejected", last_error=str(error))
            self.log(str(error), "error")
        except Exception as error:  # noqa: BLE001 — report background job failures to the UI
            self.status.update(
                phase="error",
                last_error="Model endpoint unavailable; draft remains pending"
                if isinstance(error, ProviderFailure)
                else f"{type(error).__name__}: generation could not finish; inspect the failed step",
            )
            self.log(self.status["last_error"], "error")
        finally:
            if self.content_first:
                # Keep the last compilable draft and every rejected proposal. A
                # failed attempt remains pending review and resumable, never approved.
                try:
                    self.publish()
                except ValueError as error:
                    self.log(f"Could not update retained draft: {error}", "error")
            self.snapshot['elapsed_seconds'] = self.elapsed_seconds()
            del self._attempt_started
            del self._attempt_elapsed_base
            self.status.update(
                running=False,
                paused=self.status["phase"] in ("api_paused", "evaluation_failed"),
                pause_requested=False,
                active=[],
            )
            self.persist()

    async def execute(self, steps, context, prefix=""):
        pending, done = dict(steps), set()
        known = set(self.snapshot.get("known_steps", []))
        known.update(prefix + name for name in steps)
        self.snapshot["known_steps"] = sorted(known)
        self.status["total"] = len(known)
        while pending:
            await self.gate()
            ready = [(n, s) for n, s in pending.items() if set(s.needs) <= done]
            # Model-only siblings are safe to run together. Draft operations stay ordered.
            batch = [item for item in ready if item[1].uses in ("llm.generate", "llm.content")] or ready[
                :1
            ]
            tasks = [
                asyncio.create_task(self.execute_step(n, s, context, prefix + n))
                for n, s in batch
            ]
            try:
                results = await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for (name, _), output in zip(batch, results):
                context["steps"][name] = {"output": output}
                done.add(name)
                pending.pop(name)
                self.status["completed"] = sum(
                    record.get("state") in ("complete", "fallback", "skipped")
                    for record in self.snapshot["steps"].values()
                )
            self.persist()
        return context["steps"]

    async def execute_step(self, name, step, context, path):
        await self.gate()
        cached = self.snapshot["steps"].get(path)
        if cached and cached.get("state") in ("complete", "fallback", "skipped"):
            return copy.deepcopy(cached.get("output"))
        when = resolve(step.when, context)
        if type(when) is not bool:
            raise ValueError(f"{path}: when must resolve to a boolean")
        if not when:
            self.snapshot["steps"][path] = {"state": "skipped", "output": None}
            self.log("Skipped", "info", path)
            return None
        inputs = resolve(step.inputs, context)
        record = {"inputs": inputs, "state": "running"}
        self.snapshot["steps"][path] = record
        self.log(f"{path}: {step.uses}", step=path)
        api_failures = 0
        for attempt in range(step.retries + 1):
            checkpoint = (
                (
                    copy.deepcopy(self.builder.sample),
                    self.builder.next_tick,
                    self.builder.serial,
                )
                if step.uses
                in ("remix.message", "remix.tools", "remix.draft", "remix.setup", "remix.assemble")
                else None
            )
            try:
                while True:
                    try:
                        result = await self.dispatch(step, inputs, context, path)
                        break
                    except ProviderFailure:
                        if step.uses not in ('llm.generate', 'llm.content') or api_failures >= self.state["config"].get(
                            "single_generation", {}
                        ).get("api_retries", 2):
                            raise
                        api_failures += 1
                        self.log(
                            f"Model API retry {api_failures}; attempt unchanged",
                            "warning",
                            path,
                        )
                        await self.gate()
                        await asyncio.sleep(0.1)
                record.update(state="complete", output=result)
                self.log(f"{path}: complete", "success", path)
                return result
            except asyncio.CancelledError:
                record["state"] = "cancelled"
                raise
            except EvaluationFailure as error:
                record.update(state="evaluation_failed", detail=self.clean(str(error)))
                raise
            except Exception as error:
                if isinstance(error, Rejected) and step.uses == "remix.reject":
                    record["state"] = "rejected"
                    raise
                if self.content_first:
                    self.snapshot.setdefault('failed_proposals', []).append({
                        'path': path, 'attempt': attempt, 'inputs': inputs,
                        'sample': copy.deepcopy(self.builder.sample) if checkpoint else None,
                        'error': self.clean(str(error))[:4000],
                    })
                if checkpoint is not None:
                    self.builder.sample, self.builder.next_tick, self.builder.serial = (
                        checkpoint
                    )
                record.update(
                    state="failed",
                    error=type(error).__name__,
                    detail=self.clean(str(error))[:2000]
                    if isinstance(
                        error, (ValueError, ProviderFailure, jsonschema.ValidationError)
                    )
                    else "Step execution failed",
                )
                self.log(record["detail"], "error", path)
                self.log(
                    f"{path}: {type(error).__name__}; attempt {attempt + 1}/{step.retries + 1}",
                    "error",
                    path,
                )
                if isinstance(error, ProviderFailure):
                    raise
                if attempt == step.retries:
                    if step.on_failure.action == "fallback":
                        fallback = resolve(step.on_failure.value, context)
                        if step.output_schema:
                            jsonschema.validate(
                                fallback, self.scenario.flow.schemas[step.output_schema]
                            )
                        record.update(state="fallback", output=fallback)
                        self.log(f"{path}: using configured fallback", "warning", path)
                        return record["output"]
                    if isinstance(error, Rejected):
                        raise
                    raise Rejected(
                        f"{path} rejected after {attempt + 1} attempt(s)"
                    ) from error
                inputs = {
                    **inputs,
                    "retry_feedback": {
                        "error": record["detail"],
                        "previous_output": record.get("last_output"),
                        "instruction": "Correct this output to match the requested schema exactly; do not repeat the same formatting.",
                    },
                }
                await self.gate()

    async def dispatch(self, step, inputs, context, path):
        match step.uses:
            case "llm.generate" | "llm.content":
                return await self.llm(step, inputs, context, path)
            case "flow.repeat":
                history = []
                for index in range(step.max_iterations):
                    local = {
                        **context,
                        "inputs": inputs,
                        "steps": {},
                        "iteration": {
                            "index": index,
                            "history": copy.deepcopy(history),
                        },
                    }
                    outputs = await self.execute(step.steps, local, f"{path}.{index}.")
                    history.append(
                        resolve(step.history, local)
                        if step.history
                        else copy.deepcopy(outputs)
                    )
                    finished = resolve(step.until, local)
                    if type(finished) is not bool:
                        raise ValueError("Repeat until must resolve to a boolean")
                    if finished:
                        return {"iterations": history, "resolved": True}
                raise ValueError(
                    "Conversation reached its iteration limit without resolution"
                )
            case "remix.assemble":
                result = assemble_streams(self.builder, inputs)
                if 'calls' in inputs and 'call_groups' in inputs:
                    raise ValueError('Supply calls or call_groups, not both')
                groups = inputs['call_groups'] if 'call_groups' in inputs else [inputs['calls']] if inputs.get('calls') else []
                result.update(self.simulate({'calls': groups}, start_tick=result['start_tick']))
                # Lifecycle choices belong to the generated decision. Release
                # only after every consumer in this committed batch finishes.
                self.release_windows(inputs.get('release_windows', []))
                self.publish()
                return result
            case "remix.novelty":
                if self.content_first:
                    self.builder.sample['metadata']['concept'] = copy.deepcopy(inputs['idea'])
                    return inputs['idea']
                return await self.novelty(step, inputs, context, path)
            case "remix.message":
                source = inputs.get("source", "user")
                if source not in ("user", "assistant"):
                    raise ValueError("Message source must be user or assistant")
                text = inputs["text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Message cannot be empty")
                if self.content_first:
                    validate_content(self.compiler, text)
                if not any(
                    event["op"] == "open" and event.get("window") == "chat"
                    for events in self.builder.sample["replay"].values()
                    for event in events
                ):
                    self.builder.sample["replay"].setdefault(
                        str(self.builder.next_tick), []
                    ).append({"op": "open", "window": "chat", "window-type": "chat"})
                if source == "assistant":
                    delivery = self.simulate(
                        {
                            "calls": [
                                [
                                    {
                                        "tool": "chat",
                                        "arguments": {"content": text},
                                    }
                                ]
                            ]
                        }
                    )
                    if not delivery["results"][0]["outcome"].get("ok"):
                        raise ValueError(
                            "The chat tool failed to deliver the assistant reply"
                        )
                    return {"text": text}
                self.builder.add_stream(
                    "chat",
                    f"<|msg|>User: {text}<|/msg|>\n",
                    op="append",
                    source=source,
                )
                return {"text": text}
            case "remix.setup":
                if self.content_first:
                    if inputs.get('idea'):
                        self.builder.sample['metadata']['concept'] = copy.deepcopy(inputs['idea'])
                setup_tick = str(self.builder.next_tick) if self.content_first else '0'
                for operation in inputs.get('operations', []) if self.content_first else []:
                    if not isinstance(operation, dict) or set(operation) != {'tool', 'arguments'}:
                        raise ValueError('Setup operations require tool and arguments')
                    self.builder.sample['replay'].setdefault(setup_tick, []).append(
                        {'op': 'invisible-tool-call', **operation}
                    )
                for item in inputs.get('idea', {}).get("files", []):
                    self.builder.sample["replay"].setdefault(setup_tick, []).append(
                        {
                            "op": "invisible-tool-call",
                            "tool": "file_write",
                            "arguments": {
                                "path": item["path"],
                                "content": item["content"],
                            },
                        }
                    )
                self.builder.next_tick = (
                    self.compiler.compile(self.builder.sample)["max_tick"] + 1
                )
                return {"ready": True}
            case "remix.tools":
                return self.simulate(inputs)
            case "remix.draft":
                release_tools = inputs.get("release_tool_context", False)
                if type(release_tools) is not bool or (release_tools and not inputs.get("reasoning")):
                    raise ValueError("release_tool_context requires a boolean and nonempty reasoning")
                if inputs.get("reasoning"):
                    if self.content_first:
                        validate_content(self.compiler, inputs['reasoning'])
                    self.builder.add_stream(
                        "reasoning-" + str(self.builder.serial),
                        inputs["reasoning"],
                        close=False,
                    )
                    self.builder.serial += 1
                if release_tools:
                    # The scenario explicitly promises that these notes retain
                    # the evidence required by the reply. Semantic review checks
                    # that promise; never infer it from window age alone.
                    self.close_temporary_windows(("tool-",))
                if inputs.get("response"):
                    await self.dispatch(
                        type(step)(uses="remix.message"),
                        {"source": "assistant", "text": inputs["response"]},
                        context,
                        path,
                    )
                self.close_temporary_windows(("tool-", "reasoning-"))
                result = self.publish()
                self.log(
                    "Draft ready in the dashboard; validation may still be running",
                    "success",
                    path,
                )
                return result
            case "remix.validate":
                # Read legacy checkpoints without ever validating during creation.
                return {'deferred': True, 'valid': None, 'feedback': ['Run Validate Trajectory separately']}
            case "remix.reject":
                raise Rejected(inputs.get("reason", "Rejected by scenario"))

    def release_windows(self, names):
        if (not isinstance(names, list) or len(names) > 128
                or any(not isinstance(name, str) or not name for name in names)
                or len(names) != len(set(names))):
            raise ValueError("release_windows must be a list of distinct window IDs")
        if not names:
            return
        plan = self.compiler.compile(self.builder.sample)
        windows = {window['id']: window for window in plan['windows']}
        for name in names:
            window = windows.get(name)
            if window is None or window['close'] is not None:
                raise ValueError(f"Cannot release missing or closed window {name}")
            if window['type'] in ('system', 'notes', 'tools'):
                raise ValueError(f"Cannot release persistent {window['type']} window {name}")
        tick = str(plan['max_tick'] + 1)
        self.builder.sample['replay'].setdefault(tick, []).extend(
            {'op': 'generate', 'window': name, 'content': self.compiler.special['eos']}
            for name in names
        )
        self.builder.next_tick = self.compiler.compile(self.builder.sample)['max_tick'] + 1

    def close_temporary_windows(self, prefixes):
        plan = self.compiler.compile(self.builder.sample)
        for window in plan["windows"]:
            if window["id"].startswith(prefixes) and window["close"] is None:
                self.builder.sample["replay"].setdefault(str(plan["max_tick"] + 1), []).append(
                    {"op": "generate", "window": window["id"], "content": self.compiler.special["eos"]}
                )
        self.builder.next_tick = self.compiler.compile(self.builder.sample)["max_tick"] + 1

    def simulate(self, inputs, start_tick=None):
        calls = inputs.get("calls", [])
        if not isinstance(calls, list) or len(calls) > 32:
            raise ValueError("Tool plan must contain at most 32 calls")
        results = []
        # Each inner list is an independent group; groups execute in order.
        for group in calls:
            if not isinstance(group, list) or not group or len(group) > 16:
                raise ValueError(
                    "Tool calls must be grouped into nonempty parallel batches"
                )
            existing = self.compiler.compile(self.builder.sample)['tool_calls']
            if sum(not call.get('invisible') for call in existing) + len(group) > self.config.get('max_tool_calls', 64):
                raise ValueError('Trajectory exceeds the configured tool-call budget')
            tick = str(self.builder.next_tick if start_tick is None else start_tick)
            start_tick = None
            if not any(
                event["op"] == "open" and event.get("window") == "tools"
                for events in self.builder.sample["replay"].values()
                for event in events
            ):
                self.builder.sample["replay"].setdefault(tick, []).append(
                    {"op": "open", "window": "tools", "window-type": "tools"}
                )
            names = {call["tool"] for call in group}
            if self.content_first:
                # Contracts already visible in this persistent window need not
                # be appended again on every adaptive action cycle.
                names -= {
                    name
                    for events in self.builder.sample['replay'].values()
                    for event in events
                    if event['op'] == 'show_tools' and event['window'] == 'tools'
                    for name in event['tools']
                }
            if names:
                self.builder.sample["replay"].setdefault(tick, []).append(
                    {"op": "show_tools", "window": "tools", "tools": sorted(names)}
                )
            windows = []
            for call in group:
                allowed = {'tool', 'arguments', 'outcome'}
                if self.content_first:
                    allowed |= {'duration-ticks', 'delivery'}
                if set(call) - allowed:
                    raise ValueError("Unknown tool plan field")
                window = f"tool-{self.builder.serial}"
                self.builder.serial += 1
                event = {
                    "op": "tool-call",
                    "window": window,
                    "tool": call["tool"],
                    "arguments": call["arguments"],
                }
                if self.content_first:
                    for field in ('duration-ticks', 'delivery'):
                        if field in call:
                            event[field] = call[field]
                if "outcome" in call:
                    if call["tool"] != "subagent":
                        raise ValueError(
                            "Only synthetic subagents accept authored outcomes"
                        )
                    event["outcome"] = call["outcome"]
                self.builder.sample["replay"].setdefault(tick, []).append(event)
                windows.append(window)
            plan = self.compiler.compile(self.builder.sample)
            self.builder.next_tick = plan["max_tick"] + 1
            results.extend(
                {"tool": c["tool"], "outcome": c["outcome"]}
                for c in plan["tool_calls"]
                if c.get("window") in windows
            )
        return {"results": results}

    def closed_draft(self):
        draft = copy.deepcopy(self.builder.sample)
        plan = self.compiler.compile(draft)
        for window in plan["windows"]:
            if window["close"] is None and window["type"] != "notes":
                draft["replay"].setdefault(str(plan["max_tick"] + 1), []).append(
                    {
                        "op": "generate",
                        "window": window["id"],
                        "content": self.compiler.special["eos"],
                    }
                )
        self.compiler.compile(draft)
        return draft

    def publish(self, draft=None):
        draft = draft or self.closed_draft()
        if self.content_first:
            draft['metadata']['generation_record'] = self.generation_record()
        elif self.snapshot.get('request_metrics'):
            draft['metadata']['generation_metrics'] = copy.deepcopy(self.snapshot['request_metrics'])
            draft['metadata']['generation_elapsed_seconds'] = self.elapsed_seconds()
        compiled = self.compiler.compile(draft)
        if self.content_first:
            draft['metadata']['generation_record']['diagnostics'] = compiled['diagnostics']
        if self.state_override is not None:
            draft["metadata"]["validation_level"] = self.state_override
        samples = self.state["samples"].copy()
        index = self.state.store.samples.index_of(draft["id"])
        if self.expected_draft is not None:
            if index is None or self.state["samples"][index] != self.expected_draft:
                raise ValueError(
                    "Draft changed outside generation; refusing to overwrite it"
                )
            samples[index] = draft
        else:
            samples.append(draft)
        self.history.commit(samples, f"Scenario draft: {draft['id']}")
        self.expected_draft = copy.deepcopy(draft)
        self.status["draft_id"] = draft["id"]
        self.status["draft_index"] = self.state.store.samples.index_of(draft["id"])
        self.persist()
        return {"id": draft["id"]}

    async def novelty(self, step, inputs, context, path):
        # Only this read-only step receives existing concept summaries, never the writer.
        # Legacy human samples contribute user-input summaries without exposing full replay.
        with self.state.store.connect() as db:
            rows = db.execute("""
                SELECT coalesce(json_extract(s.source,'$.metadata.concept'),
                    json_object('request', coalesce((SELECT substr(group_concat(json_extract(e.value,'$.content'), ' '),1,2000)
                        FROM json_each(s.source,'$.replay') t, json_each(t.value) e
                        WHERE json_extract(e.value,'$.op')='append' AND json_extract(e.value,'$.source')='user'), '')))
                FROM samples s ORDER BY s.position
            """)
            candidates = [json.loads(row[0]) for row in rows]

        def summary(value):
            return {
                key: value[key]
                for key in ("problem", "situation", "success_criteria", "request")
                if key in value
            }

        candidates = [summary(value) for value in candidates]
        idea = inputs["idea"]
        best = None
        for attempt in range(step.attempts):
            report = {"similarity": 0, "feedback": "No existing concepts"}
            batches, batch = [], []

            def novelty_input(values, idea=idea):
                return {
                    "idea": summary(idea),
                    "existing_concepts": values,
                    "task": "Score semantic duplication from 0 to 1, including solution and interaction similarity.",
                }

            for candidate in candidates:
                proposed = [*batch, candidate]
                if batch and (
                    len(proposed) > 24
                    or step.max_input_tokens
                    and self.prompt_tokens(
                        step, novelty_input(proposed), context, NOVELTY, step.prompt
                    )
                    > step.max_input_tokens
                ):
                    batches.append(batch)
                    batch = []
                batch.append(candidate)
            if batch:
                batches.append(batch)
            for batch in batches:
                candidate_report = await self.llm(
                    step, novelty_input(batch), context, path, NOVELTY
                )
                if candidate_report["similarity"] >= report["similarity"]:
                    report = candidate_report
            if best is None or report["similarity"] < best[0]:
                best = (report["similarity"], idea)
            if report["similarity"] < inputs.get("threshold", 0.85):
                break
            if attempt + 1 < step.attempts:
                origin = inputs["idea_step"]
                definition = self.scenario.flow.steps[origin]
                original_inputs = resolve(definition.inputs, context)
                idea = await self.llm(
                    definition,
                    {**original_inputs, "avoid": idea, "feedback": report["feedback"]},
                    context,
                    path + ".retry",
                )
        else:
            if not step.fallback or best[0] >= 1:
                raise Rejected("Novelty attempts exhausted")
            idea = best[1]
            self.log(
                "Novelty threshold relaxed; using least similar candidate",
                "warning",
                path,
            )
            self.builder.sample["metadata"]["novelty_relaxed"] = True
        self.builder.sample["metadata"]["concept"] = idea
        if idea.get("identifier"):
            self.assign_identifier(idea["identifier"])
        return idea
