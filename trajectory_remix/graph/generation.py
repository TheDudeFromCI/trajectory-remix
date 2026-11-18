"""Durable shared event/review/repair driver over the existing FIFO services."""

import asyncio
import json
from copy import deepcopy

import jsonschema

from . import (
    adaptive_driver,
    adaptive_flow,
    build_agent,
    flow_store,
    repair_policy,
    reviews,
    tool_listing,
    two_pass,
)
from .commands import merge_patch
from .flow import append_event, chat_echo, event_payload, prepare_plan
from .jobs import Fenced, OwnershipConflict
from .models import GraphSource, ProjectSettings, canonical
from .progress import label
from .provider import invalid_reason
from .sampling import SamplerService
from .schema_errors import explain
from .storage import RevisionConflict
from .validation import diagnose


class GenerationService:
    def __init__(self, jobs, sampler, bulk, transport):
        self.jobs, self.store, self.sampler, self.bulk = jobs, jobs.store, sampler, bulk
        self.transport = transport
        self.calls = set()

    def settings_for(self, job):
        config = self.jobs.inspect(job)["config"]
        return ProjectSettings.model_validate(
            config["settings"] if "settings" in config else config["options"]["settings"]
        )

    def start(self, requests, instructions):
        frozen = self.sampler.editor()
        settings_record = self.store.settings(frozen["settings_revision"])
        settings = settings_record.settings
        if "generator" not in settings.endpoint_roles:
            raise ValueError("Configure the Generator endpoint in Project Settings")
        if settings.generation.fast and "fast" in settings.endpoint_roles:
            reviews.review_schema(settings, "fast")
            if settings.generation.critical and "critical" in settings.endpoint_roles:
                reviews.review_schema(settings, "critical")
        instructions = deepcopy(instructions)
        if "plan" in instructions and "flow_yaml" in instructions:
            raise ValueError("Choose plan or flow_yaml, not both")
        if "_flow_snapshot" in instructions:
            raise ValueError("_flow_snapshot is reserved")
        if "plan" not in instructions:
            # A per-run flow_yaml overrides the project flow stored in the database.
            text = instructions.get("flow_yaml")
            if "flow_yaml" not in instructions:
                text = flow_store.project_text(self.store)
            instructions["_flow_snapshot"] = adaptive_flow.load_flow(text=text)
        # Freeze concrete plans for ALL proposals before reserving attempts.
        proposals = [self.sampler.propose(**r, frozen=frozen) for r in requests]
        method = settings.generation.method
        if method in two_pass.METHODS:
            if "plan" in instructions:
                raise ValueError("Two-pass generation uses the project flow, not a plan")
            if method == "one_pass_agent" and (
                settings.generation.agent_role not in settings.endpoint_roles
            ):
                raise ValueError("Configure the endpoint role that runs the one-pass agent")
            snapshot = instructions["_flow_snapshot"]
            plans = {
                p["id"]: two_pass.plan(p["proposal"], settings, snapshot, method) for p in proposals
            }
        else:
            plans = {
                p["id"]: prepare_plan(p["proposal"], settings, instructions) for p in proposals
            }
        return self.jobs.create_run(
            dict(
                instructions=deepcopy(instructions),
                sampler=frozen,
                plans=plans,
                driver="event-flow/1",
            ),
            proposals,
            settings_record=settings_record,
        )

    def start_review(
        self, query, *, mode="standard", repair=True, trajectory=None, expected_revision=None
    ):
        if mode not in {"standard", "force_fast", "force_critical"} or type(repair) is not bool:
            raise ValueError("Invalid review mode")
        settings = self.store.settings().settings
        for tier in ("fast", "critical"):
            if tier in settings.endpoint_roles:
                reviews.review_schema(settings, tier)
        options = dict(mode=mode, repair=repair, settings=settings.model_dump(mode="json"))
        if trajectory is None:
            return self.bulk.start(query, "review_repair", options)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.store._read(db, trajectory)
            self.store._check_revision(trajectory, expected_revision, record)
            job = self.jobs._create(
                db, "bulk", dict(query={}, action="review_repair", options=options), 1
            )
            db.execute(
                "INSERT INTO graph_bulk_items VALUES(?,0,?,?,'pending',NULL,NULL)",
                (job, trajectory, record.revision),
            )
            return job

    def _owners(self, job):
        with self.store.connect() as db:
            return [
                r[0]
                for r in db.execute("SELECT trajectory_id FROM graph_owners WHERE job_id=?", (job,))
            ]

    def _pending_request(self, job, trajectory):
        with self.store.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM graph_requests WHERE job_id=? AND trajectory_id=? AND state IN ('queued','running','ready')",
                    (job, trajectory),
                ).fetchone()
                is not None
            )

    def _save(self, job, source, cp, *, ticket=None, outcome=None):
        cp = deepcopy(cp)
        epoch = ticket.epoch if ticket else cp["_epoch"]
        revision = ticket.payload["source_revision"] if ticket else cp["_revision"]
        cp.pop("_epoch", None)
        cp.pop("_revision", None)
        step = ticket.step if ticket else f"{source.id}:{cp['serial']}:local"
        cp["serial"] += 1
        accepted = self.jobs.accept(
            job,
            epoch,
            source.id,
            step,
            source,
            revision,
            cp,
            request=ticket,
            outcome=outcome,
            bulk_position=cp.get("bulk_position"),
        )
        if outcome is not None:
            self.jobs.progress(
                job,
                f"Trajectory settled: {label(source.id)} outcome={label(outcome)} events={len(source.events)}",
            )
        return accepted

    def _phase(self, job, source, cp, phase, tier=None, *, outcome=None):
        cp.update(phase=phase, tier=tier, errors=0)
        if phase == "repair":
            cp["history"] = []
            cp["repair_base"] = reviews.payload(source, self.settings_for(job), tier, "repair")[
                "messages"
            ]
        self._save(job, source, cp, outcome=outcome)
        self.jobs.progress(
            job,
            f"Trajectory {label(source.id)} entered {label(phase)}"
            + (f" ({label(tier)})" if tier else ""),
        )

    def _after_generation(self, job, source, cp):
        settings = self.settings_for(job)
        data = source.to_dict()
        data["metadata"]["stage"] = 0
        source = GraphSource.model_validate(data)
        if settings.generation.fast and "fast" in settings.endpoint_roles:
            self._phase(job, source, cp, "review", "fast")
        else:
            self._phase(job, source, cp, "done", outcome="pass")

    def _admit(self, job, epoch=None):
        j = self.jobs.inspect(job)
        if epoch is not None and j["epoch"] != epoch:
            raise Fenced("Worker epoch changed")
        # Only this handoff's explicitly prepared runs have model access. Generic
        # /runs and historical sampler snapshots cannot silently become live jobs.
        if j["config"]["config"].get("driver") != "event-flow/1":
            self.jobs.control(job, "pause")
            self.jobs.append_log(job, "No event Flow snapshot; start a new generation run.")
            return
        self.settings_for(job)
        for offset in range(0, j["total"], 100):
            for attempt in self.jobs.attempts(job, offset=offset):
                if attempt["state"] != "pending":
                    continue
                proposal = attempt["proposal"]
                data = dict(
                    format="kidra-trajectory-graph/1",
                    id=attempt["trajectory_id"],
                    windows=j["config"]["config"]["plans"][proposal["id"]]["windows"],
                    events={},
                    metadata=SamplerService.metadata(proposal),
                )
                data["metadata"].update(stage=-3, split="Auto")
                try:
                    self.jobs.start_attempt(
                        job, attempt["ordinal"], data, expected_epoch=j["epoch"]
                    )
                    self.jobs.progress(
                        job,
                        f"Trajectory admitted: {label(attempt['trajectory_id'])} ({attempt['ordinal'] + 1}/{j['total']})",
                    )
                except OwnershipConflict:
                    return  # Attached cap includes paused sources; no replacement.

    def _manual_admit(self, job, epoch=None):
        j = self.jobs.inspect(job)
        if epoch is not None and j["epoch"] != epoch:
            raise Fenced("Worker epoch changed")
        for offset in range(0, j["total"], 100):
            for item in self.bulk.results(job, offset=offset):
                if item["state"] != "pending":
                    continue
                ident = item["trajectory_id"]
                try:
                    with self.store.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        self.jobs._fence(db, job, j["epoch"])
                        # Pause/restart preserves owners and checkpoints. Existing
                        # work resumes from its latest accepted patch, not initial CAS.
                        owned = db.execute(
                            "SELECT job_id FROM graph_owners WHERE trajectory_id=?", (ident,)
                        ).fetchone()
                        if owned and owned[0] == job:
                            db.execute(
                                "UPDATE graph_bulk_items SET state='working' WHERE job_id=? AND position=?",
                                (job, item["position"]),
                            )
                            continue
                        record = self.store._read(db, ident)
                        operation = reviews.manual_operation(
                            record.source.metadata.get("stage", -3),
                            j["config"]["options"]["mode"],
                            j["config"]["options"]["repair"],
                        )
                        if (
                            not operation
                            or operation[0] not in self.settings_for(job).endpoint_roles
                        ):
                            self.bulk._settle_item(
                                db,
                                job,
                                item["position"],
                                "skipped",
                                "Stage skipped or review endpoint unconfigured",
                            )
                            continue
                        self.jobs._acquire(db, job, ident, item["revision"])
                        db.execute(
                            "UPDATE graph_bulk_items SET state='working' WHERE job_id=? AND position=?",
                            (job, item["position"]),
                        )
                    cp = dict(
                        serial=0,
                        phase=operation[1],
                        tier=operation[0],
                        errors=0,
                        manual=True,
                        bulk_position=item["position"],
                        _epoch=j["epoch"],
                        _revision=record.revision,
                    )
                    if operation[1] == "repair":
                        cp.update(
                            history=[],
                            repair_base=reviews.payload(
                                record.source, self.settings_for(job), operation[0], "repair"
                            )["messages"],
                        )
                    self._save(job, record.source, cp)
                except (OwnershipConflict, RevisionConflict, KeyError):
                    with self.store.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        self.jobs._fence(db, job, j["epoch"])
                        self.bulk._settle_item(
                            db, job, item["position"], "skipped", "Locked or edited since selection"
                        )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.jobs._fence(db, job, j["epoch"])
            if not db.execute(
                "SELECT 1 FROM graph_bulk_items WHERE job_id=? AND state IN ('pending','working')",
                (job,),
            ).fetchone():
                db.execute("UPDATE graph_jobs SET state='completed' WHERE id=?", (job,))
                self.jobs._notify(db, job)

    def _prepare(self, job, ident, epoch=None):
        if self._pending_request(job, ident):
            return
        record = self.store.read(ident)
        source = record.source
        cp = self.jobs.checkpoint(job, ident)
        settings = self.settings_for(job)
        epoch = self.jobs.inspect(job)["epoch"] if epoch is None else epoch
        if cp is None:  # Crash immediately after atomic start_attempt is safe.
            j = self.jobs.inspect(job)
            if j["kind"] == "bulk":
                with self.store.connect() as db:
                    item = db.execute(
                        "SELECT position FROM graph_bulk_items WHERE job_id=? AND trajectory_id=?",
                        (job, ident),
                    ).fetchone()
                operation = reviews.manual_operation(
                    source.metadata.get("stage", -3),
                    j["config"]["options"]["mode"],
                    j["config"]["options"]["repair"],
                )
                cp = dict(
                    serial=0,
                    phase=operation[1],
                    tier=operation[0],
                    errors=0,
                    manual=True,
                    bulk_position=item[0],
                )
                if cp["phase"] == "repair":
                    cp.update(
                        history=[],
                        repair_base=reviews.payload(source, settings, cp["tier"], "repair")[
                            "messages"
                        ],
                    )
            else:
                cp = dict(serial=0, phase="event", index=0, errors=0, manual=False)
        cp.update(_epoch=epoch, _revision=record.revision)
        if cp["phase"] == "event":
            if cp.get("echo"):
                echo = chat_echo(source, cp.pop("echo"))
                if echo:
                    content = echo.pop("content")
                    source = append_event(source, echo, {"content": content}, settings)
                    if any(d.severity == "error" for d in diagnose(source, settings)):
                        raise ValueError("Chat helper structural error")
                self._save(job, source, cp)
                return
            with self.store.connect() as db:
                proposal = json.loads(
                    db.execute(
                        "SELECT proposal FROM graph_attempts WHERE trajectory_id=?", (ident,)
                    ).fetchone()[0]
                )
            plan = self.jobs.inspect(job)["config"]["config"]["plans"][proposal["id"]]
            if plan.get("method") in two_pass.METHODS:
                if cp.get("assembled"):
                    self._after_generation(job, source, cp)
                    return
                if plan["method"] == "one_pass_agent" and "draft" in cp:
                    self._start_agent(job, source, cp, plan, settings)
                    return
                payload = two_pass.payload(cp, plan, settings)
            elif "flow" in plan:
                payload = adaptive_driver.prepare(self, job, source, cp, plan, settings)
                if payload is None:
                    return
            else:
                tasks = plan["tasks"]
                if cp["index"] >= len(tasks):
                    self._after_generation(job, source, cp)
                    return
                task = deepcopy(tasks[cp["index"]])
                task["envelope"]["after"] = [
                    dep + ":echo" if dep + ":echo" in source.events else dep
                    for dep in task["envelope"].get("after", [])
                ]
                payload = event_payload(source, task, settings, cp["errors"] > 0)
                payload["task"] = task
            tier = "generator"
        elif cp["phase"] == build_agent.PHASE:
            plan = self._plan(job, ident)
            payload = build_agent.payload(cp, plan, settings)
            if (
                len(canonical(payload["messages"]).encode())
                >= settings.generation.repair_context_bytes
            ):
                self._agent_round(job, source, cp, plan)
                return
            tier = cp["tier"]
        else:
            tier = cp["tier"]
            payload = reviews.payload(
                source, settings, tier, cp["phase"], correction=cp["errors"] > 0
            )
            if cp["phase"] == "repair":
                # Original complete snapshot + continuing tool history. Never
                # silently rebuild/compact a repair session after each patch.
                payload["messages"] = cp["repair_base"] + cp["history"]
                if (
                    len(canonical(payload["messages"]).encode())
                    >= settings.generation.repair_context_bytes
                ):
                    self._next_round(job, source, cp)
                    return
        endpoint = settings.endpoint_roles[tier]
        payload.update(
            endpoint_config=settings.endpoints[endpoint].model_dump(mode="json"),
            checkpoint=cp,
            source_revision=record.revision,
            source_hash=source.intrinsic_hash,
        )
        request_id = self.jobs.enqueue(
            job, ident, endpoint, f"{ident}:{cp['serial']}", payload, expected_epoch=epoch
        )
        with self.store.connect() as db:
            paused = db.execute(
                "SELECT paused FROM graph_endpoints WHERE id=?", (endpoint,)
            ).fetchone()[0]
        self.jobs.progress(
            job,
            f"Request queued: {label(request_id)} trajectory={label(ident)} "
            f"operation={label(payload['operation'])} endpoint={label(endpoint)}"
            + (
                "; endpoint paused, manual Resume required"
                if paused
                else "; awaiting endpoint slot"
            ),
        )

    def _next_round(self, job, source, cp, ticket=None):
        """A repair that outgrew its context continues as a fresh round, up to the cap."""
        settings = self.settings_for(job)
        base = reviews.payload(source, settings, cp["tier"], "repair")["messages"]
        if not repair_policy.new_round(cp, settings, base):
            self._repair_failure(job, source, cp, "round_limit", ticket)
            return
        self._save(job, source, cp, ticket=ticket)
        self.jobs.progress(
            job, f"Repair round {cp['round']} started: trajectory={label(source.id)}"
        )

    def _plan(self, job, ident):
        with self.store.connect() as db:
            proposal = json.loads(
                db.execute(
                    "SELECT proposal FROM graph_attempts WHERE trajectory_id=?", (ident,)
                ).fetchone()[0]
            )
        return self.jobs.inspect(job)["config"]["config"]["plans"][proposal["id"]]

    @staticmethod
    def _can_repair(settings):
        return settings.generation.repair and "fast" in settings.endpoint_roles

    def _assembled(self, ticket, source, broken, cp):
        """Commit an assembled graph. Structural errors go straight to Fast repair."""
        self.jobs.progress(
            ticket.job_id,
            f"Graph assembled: trajectory={label(source.id)} events={len(source.events)}"
            + (f" structural_errors={len(broken)}" if broken else ""),
        )
        if not broken:
            cp["assembled"] = True
            self._save(ticket.job_id, source, cp, ticket=ticket)
            return
        settings = self.settings_for(ticket.job_id)
        data = source.to_dict()
        data["metadata"].update(
            stage=-1, generation_failure=dict(kind="assembled_with_structural_errors")
        )
        source = GraphSource.model_validate(data)
        cp.update(phase="repair", tier="fast", errors=0, history=[], round=1)
        cp["repair_base"] = reviews.payload(source, settings, "fast", "repair")["messages"]
        self._save(ticket.job_id, source, cp, ticket=ticket)

    def _start_agent(self, job, source, cp, plan, settings):
        spec = deepcopy(build_agent.EMPTY)
        cp.update(phase=build_agent.PHASE, tier=settings.generation.agent_role, errors=0)
        cp.update(history=[], round=1, spec=spec)
        cp["agent_base"] = build_agent.base(plan, settings, spec, cp["draft"])
        self._save(job, source, cp)
        self.jobs.progress(job, f"Agent build started: trajectory={label(source.id)}")

    def _agent_round(self, job, source, cp, plan, ticket=None):
        """A session that outgrew its budget continues as a fresh round on the current spec."""
        settings = self.settings_for(job)
        if cp["round"] >= settings.generation.repair_rounds:
            self._agent_end(job, source, cp, "round_limit", ticket)
            return
        note = "Earlier rounds built the spec shown; continue from it."
        cp.update(round=cp["round"] + 1, history=[], errors=0)
        cp["agent_base"] = build_agent.base(plan, settings, cp["spec"], cp["draft"], note)
        self._save(job, source, cp, ticket=ticket)
        self.jobs.progress(job, f"Agent round {cp['round']} started: trajectory={label(source.id)}")

    def _agent_end(self, job, source, cp, reason, ticket=None, detail=None):
        data = source.to_dict()
        data["metadata"]["stage"] = -3
        data["metadata"]["agent_outcome"] = dict(
            reason=reason, **({"detail": detail} if detail else {})
        )
        cp["phase"] = "done"
        self._save(job, GraphSource.model_validate(data), cp, ticket=ticket, outcome="fail")

    def _agent_step(self, ticket, source, record, cp, body, complete):
        settings = self.settings_for(ticket.job_id)
        action = body["action"]
        if action == "give_up":
            self._agent_end(ticket.job_id, source, cp, "gave_up", ticket, body["reason"])
            return
        if action == "finish":
            reply = self._agent_finish(ticket, source, record, cp, body, complete, settings)
            if reply is None:
                return
        elif action == "view":
            reply = build_agent.view(source, cp["spec"], settings)
        else:
            plan = self._plan(ticket.job_id, ticket.trajectory_id)
            try:
                spec = build_agent.changed(cp["spec"], body)
                built = build_agent.build(source, spec, plan)
            except (ValueError, KeyError) as error:
                reply = dict(accepted=False, error=str(error)[:1500])
            else:
                cp["spec"], source = spec, built
                reply = build_agent.summary(source, settings)
        cp["history"].extend(
            [
                dict(role="assistant", content=canonical(body)),
                dict(role="user", content=canonical(reply)),
            ]
        )
        self._save(ticket.job_id, source, cp, ticket=ticket)

    def _agent_finish(self, ticket, source, record, cp, body, complete, settings):
        """Sign off a passing verdict (returns None), or return the reply that keeps going."""
        broken = build_agent.errors(source, settings)
        if broken:
            return dict(accepted=False, errors=[d.model_dump(mode="json") for d in broken])
        try:
            quality = reviews.verdict(body["review"], source)
        except ValueError as error:
            return dict(accepted=False, error=str(error))
        if quality == "fail":
            return dict(
                accepted=False, still_failing=repair_policy.still_failing(body["review"])["content"]
            )
        source, _ = reviews.signoff(
            source, record, settings, "critical", build_agent.OPERATION, body["review"],
            ticket.endpoint, complete.get("model"), settings.endpoints[ticket.endpoint].model,
        )  # fmt: skip
        cp["phase"] = "done"
        self._save(ticket.job_id, source, cp, ticket=ticket, outcome="pass")
        self.jobs.progress(ticket.job_id, f"Agent signed off: trajectory={label(source.id)}")
        return None

    def _repair_failure(self, job, source, cp, reason, ticket=None, detail=None):
        data = source.to_dict()
        data.setdefault("metadata", {})["stage"] = -3
        outcome = dict(tier=cp["tier"], outcome=reason, source_hash=source.intrinsic_hash)
        if detail:
            outcome["reason"] = detail
        data["metadata"].setdefault("repair_outcomes", []).append(outcome)
        cp["phase"] = "done"
        self._save(job, GraphSource.model_validate(data), cp, ticket=ticket, outcome="fail")

    def _invalid(self, ticket, source, cp):
        settings = self.settings_for(ticket.job_id)
        cp["errors"] += 1
        self.jobs.progress(
            ticket.job_id,
            f"Invalid structured output; correction {cp['errors']}/{settings.generation.format_retries}: {label(ticket.payload.get('operation', cp['phase']))} trajectory={label(source.id)}",
        )
        if cp["errors"] > settings.generation.format_retries:
            if cp["phase"] == build_agent.PHASE:
                self._agent_end(ticket.job_id, source, cp, "invalid_output", ticket)
            elif cp["phase"] == "event":
                data = source.to_dict()
                data["metadata"]["stage"] = -1
                data["metadata"]["generation_failure"] = {
                    "kind": "invalid_provider_output",
                    "event_index": cp["index"],
                }
                # An empty graph has nothing to repair and would pass structurally.
                if self._can_repair(settings) and source.events:
                    cp.update(phase="repair", tier="fast", errors=0, history=[])
                    cp["repair_base"] = reviews.payload(
                        GraphSource.model_validate(data), settings, "fast", "repair"
                    )["messages"]
                    self._save(ticket.job_id, GraphSource.model_validate(data), cp, ticket=ticket)
                else:
                    cp["phase"] = "done"
                    self._save(
                        ticket.job_id,
                        GraphSource.model_validate(data),
                        cp,
                        ticket=ticket,
                        outcome="fail",
                    )
            else:
                # No quality verdict exists. Consume invalid body, preserve stage,
                # then pause for explicit Resume and a fresh bounded correction.
                cp["errors"] = 0
                self._save(ticket.job_id, source, cp, ticket=ticket)
                self.jobs.control(ticket.job_id, "pause")
                self.jobs.append_log(
                    ticket.job_id,
                    "Review/repair output invalid; no verdict recorded. Manual Resume required.",
                )
        else:
            if cp["phase"] in ("repair", build_agent.PHASE):
                cp["history"].append(
                    dict(
                        role="user",
                        content="Previous reply violated the output contract. Return a complete valid inspection, patch or verdict.",
                    )
                )
            self._save(ticket.job_id, source, cp, ticket=ticket)

    def consume(self, ticket):
        complete = self.jobs.result(ticket)
        cp = deepcopy(ticket.payload["checkpoint"])
        record = self.store.read(ticket.trajectory_id)
        source = record.source
        if record.revision != ticket.payload["source_revision"]:
            raise Fenced("Source changed while request was in progress")
        settings = self.settings_for(ticket.job_id)
        if complete.get("context_limit"):
            if cp["phase"] == build_agent.PHASE:
                plan = self._plan(ticket.job_id, ticket.trajectory_id)
                self._agent_round(ticket.job_id, source, cp, plan, ticket)
            elif cp["phase"] == "repair":
                self._next_round(ticket.job_id, source, cp, ticket)
            else:
                self._save(ticket.job_id, source, cp, ticket=ticket)
                self.jobs.control(ticket.job_id, "pause")
                self.jobs.append_log(
                    ticket.job_id,
                    (
                        f"Local context guard ({ticket.payload['operation']}): "
                        f"about {complete['local_budget']['input_tokens_estimate']} input tokens "
                        f"({complete['local_budget']['input_bytes']} bytes at "
                        f"{complete['local_budget']['bytes_per_token']} bytes/token) exceed the "
                        f"{complete['local_budget']['input_ceiling']}-token allowance. "
                        "No provider call made. Reduce prompt scope or change the endpoint budget; "
                        "active runs keep frozen settings."
                        if complete.get("local_budget")
                        else "Provider context limit; no quality verdict. Manual Resume required."
                    ),
                )
            return
        body = complete.get("output")
        try:
            if complete.get("invalid_output"):
                raise ValueError(invalid_reason(complete))
            problem = explain(ticket.payload["schema"], body)
            if problem:
                raise ValueError(problem)
            if ticket.payload["operation"] == "flow_select":
                selected_source, selected_task, selection = adaptive_flow.accept_selection(
                    source,
                    body,
                    ticket.payload["flow_plan"],
                    settings,
                    shown=cp["flow"].get("shown_tools"),
                )
            if ticket.payload["operation"] == two_pass.ASSEMBLE:
                plan = self._plan(ticket.job_id, ticket.trajectory_id)
                assembled, broken = two_pass.assemble(source, body, plan, settings)
                if broken and not self._can_repair(settings):
                    raise ValueError(
                        "Structural errors; fix them: "
                        + "; ".join(f"{d.code}: {d.message}" for d in broken[:8])
                    )
            if ticket.payload["operation"] == tool_listing.OPERATION:
                listed_flow = deepcopy(cp["flow"])
                listed_source, listing = tool_listing.accept(
                    source, body, ticket.payload["flow_plan"], settings, listed_flow
                )
            if cp["phase"] == "review":
                quality = reviews.verdict(body, source)
                if quality == "pass" and any(
                    d.severity == "error" for d in diagnose(source, settings)
                ):
                    raise ValueError("A structural error cannot receive a passing signoff")
            elif cp["phase"] == "repair" and body["action"] == "verdict":
                quality = reviews.verdict(body["review"], source)
                if quality == "fail":
                    # Not a failure: the repairer keeps working, or gives up explicitly.
                    cp["errors"] = 0
                    cp["failed_review"] = body["review"]
                    cp["history"].extend(
                        [
                            dict(role="assistant", content=canonical(body)),
                            repair_policy.still_failing(body["review"]),
                        ]
                    )
                    self._save(ticket.job_id, source, cp, ticket=ticket)
                    return
                if quality == "pass" and any(
                    d.severity == "error" for d in diagnose(source, settings)
                ):
                    raise ValueError("Repair must resolve structural errors before passing")
        except (jsonschema.ValidationError, ValueError, TypeError, KeyError) as error:
            cp["flow_error"] = str(error)[:2000]
            self._invalid(ticket, source, cp)
            return
        cp["errors"] = 0
        if cp["phase"] == build_agent.PHASE:
            self._agent_step(ticket, source, record, cp, body, complete)
            return
        if ticket.payload["operation"] == two_pass.DRAFT:
            cp["draft"] = body["draft"]
            self._save(ticket.job_id, two_pass.with_draft(source, body["draft"]), cp, ticket=ticket)
            self.jobs.progress(ticket.job_id, f"Draft written: trajectory={label(source.id)}")
            return
        if ticket.payload["operation"] == two_pass.ASSEMBLE:
            self._assembled(ticket, assembled, broken, cp)
            return
        if ticket.payload["operation"] == tool_listing.OPERATION:
            cp["flow"] = listed_flow
            adaptive_driver.advance(cp, ticket.payload["flow_plan"])
            self._save(ticket.job_id, listed_source, cp, ticket=ticket)
            self.jobs.progress(
                ticket.job_id,
                f"Tools listed: {len(listing['shown'])} shown, {len(listing['chosen'])} chosen",
            )
            return
        if ticket.payload["operation"] == "flow_select":
            cp["flow"].update(output=selection, task=selected_task)
            adaptive_driver.advance(cp, ticket.payload["flow_plan"])
            self._save(ticket.job_id, selected_source, cp, ticket=ticket)
            self.jobs.progress(
                ticket.job_id,
                "Flow selected: " + (selected_task["id"] if selected_task else "finish"),
            )
            return
        if cp["phase"] == "event":
            task = ticket.payload["task"]
            source = append_event(source, task, body, settings)
            # A wrapper bug cannot be repaired by asking the model repeatedly.
            errors = [d for d in diagnose(source, settings) if d.severity == "error"]
            if errors:
                raise ValueError("Flow wrapper structural error: " + errors[0].code)
            cp["index"] += 1
            if "flow_plan" in ticket.payload:
                adaptive_driver.advance(cp, ticket.payload["flow_plan"])
            cp["echo"] = task["id"]
            self._save(ticket.job_id, source, cp, ticket=ticket)
            self.jobs.progress(
                ticket.job_id,
                f"Event committed: {label(task['id'])} trajectory={label(source.id)} total_events={len(source.events)}",
            )
            return
        if cp["phase"] == "repair" and body["action"] == "give_up":
            self.jobs.progress(
                ticket.job_id, f"Repair given up as not feasible: trajectory={label(source.id)}"
            )
            self._repair_failure(
                ticket.job_id, source, cp, "unfeasible", ticket, detail=body["reason"]
            )
            return
        if cp["phase"] == "repair" and body["action"] != "verdict":
            self._repair_action(ticket, source, cp, body)
            return
        review = body["review"] if cp["phase"] == "repair" else body
        source, result = reviews.signoff(
            source,
            record,
            settings,
            cp["tier"],
            cp["phase"],
            review,
            ticket.endpoint,
            complete.get("model"),
            settings.endpoints[ticket.endpoint].model,
        )
        self.jobs.progress(
            ticket.job_id,
            f"{label(cp['tier'])} {label(cp['phase'])} verdict: {label(result)} trajectory={label(source.id)}",
        )
        if cp["phase"] == "repair":
            if result == "fail":
                self._repair_failure(ticket.job_id, source, cp, "negative_verdict", ticket)
            else:
                if (
                    not cp["manual"]
                    and cp["tier"] == "fast"
                    and settings.generation.critical
                    and "critical" in settings.endpoint_roles
                ):
                    cp.update(phase="review", tier="critical", errors=0)
                    self._save(ticket.job_id, source, cp, ticket=ticket)
                else:
                    cp["phase"] = "done"
                    self._save(ticket.job_id, source, cp, ticket=ticket, outcome="pass")
            return
        manual = cp["manual"]
        repair_enabled = (
            self.jobs.inspect(ticket.job_id)["config"]["options"]["repair"]
            if manual
            else settings.generation.repair
        )
        if result == "fail" and repair_enabled:
            cp.update(phase="repair", errors=0, history=[], round=1, failed_review=review)
            cp["repair_base"] = reviews.payload(source, settings, cp["tier"], "repair")["messages"]
            cp["repair_base"].append(
                dict(role="user", content=canonical({"failed_review": review}))
            )
            self._save(ticket.job_id, source, cp, ticket=ticket)
        elif (
            not manual
            and result == "pass"
            and cp["tier"] == "fast"
            and settings.generation.critical
            and "critical" in settings.endpoint_roles
        ):
            cp.update(tier="critical", errors=0)
            self._save(ticket.job_id, source, cp, ticket=ticket)
        else:
            cp["phase"] = "done"
            self._save(ticket.job_id, source, cp, ticket=ticket, outcome=result)

    def _repair_action(self, ticket, source, cp, body):
        settings = self.settings_for(ticket.job_id)
        reply = {}
        ident = body.get("event_id", body.get("window_id"))
        try:
            if body["action"] == "inspect":
                reply = repair_policy.inspect(source, ident)
            elif body["action"] == "view":
                reply = repair_policy.view(source, settings)
            else:
                if body["expected_source_hash"] != source.intrinsic_hash:
                    raise ValueError("Patch source hash is stale")
                data = source.to_dict()
                if body["action"] == "remove_event":
                    del data["events"][ident]  # Keep broken references explicit.
                else:
                    collection = "windows" if body["action"] == "patch_window" else "events"
                    data[collection][ident] = merge_patch(
                        data[collection].get(ident, {}), body["patch"]
                    )
                prepared = GraphSource.model_validate(data)
                source = prepared
                reply = dict(
                    accepted=True,
                    source_hash=source.intrinsic_hash,
                    diagnostics=[d.model_dump(mode="json") for d in diagnose(source, settings)],
                )
        except (ValueError, KeyError):
            reply = {
                "accepted": False,
                "error": "Invalid event, source hash or graph patch; inspect and correct within this session.",
            }
        cp["history"].extend(
            [
                dict(role="assistant", content=canonical(body)),
                dict(role="user", content=canonical(reply)),
            ]
        )
        self._save(ticket.job_id, source, cp, ticket=ticket)

    async def _call(self, endpoint):
        try:
            ticket = await self.jobs.execute_one(endpoint, self.transport)
            if ticket:
                self.consume(ticket)
        except (Fenced, asyncio.CancelledError):
            pass
        except Exception:
            # Unknown local wrapper/transport-adapter failures are resumable.
            if (
                "ticket" in locals()
                and ticket
                and self.jobs.inspect(ticket.job_id)["state"] == "running"
            ):
                self.jobs.control(ticket.job_id, "pause")
                self.jobs.append_log(
                    ticket.job_id,
                    "Flow interrupted; completed commits retained. Manual Resume required.",
                )

    def dispatch(self):
        # Claims remain the sole ordering/cap authority. Each task is one queue
        # item, transport retries remain inside execute_one, never at the tail.
        with self.store.connect() as db:
            endpoints = [dict(r) for r in db.execute("SELECT * FROM graph_endpoints")]
        for endpoint in endpoints:
            active = sum(
                1 for task in self.calls if not task.done() and task.get_name() == endpoint["id"]
            )
            if endpoint["paused"]:
                continue
            for _ in range(max(0, endpoint["concurrency"] - active)):
                task = asyncio.create_task(self._call(endpoint["id"]), name=endpoint["id"])
                self.calls.add(task)
                task.add_done_callback(self.calls.discard)

    async def run(self, jobs, job):
        epoch = self.jobs.inspect(job)["epoch"]
        while (
            self.jobs.inspect(job)["state"] == "running"
            and self.jobs.inspect(job)["epoch"] == epoch
        ):
            if self.jobs.inspect(job)["kind"] == "run":
                self._admit(job, epoch)
            else:
                self._manual_admit(job, epoch)
            if self.jobs.inspect(job)["state"] != "running":
                break
            for ident in self._owners(job):
                self._prepare(job, ident, epoch)
            self.dispatch()
            await asyncio.sleep(0.025)
