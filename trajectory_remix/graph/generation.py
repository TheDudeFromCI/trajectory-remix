"""Durable shared event/review/repair driver over the existing FIFO services."""

import asyncio
import json
from copy import deepcopy

import jsonschema

from . import (
    assembly_fix,
    draft_assembly,
    phase_barrier,
    repair_policy,
    reviews,
    spec_normalize,
    trajectory_hold,
)
from .commands import merge_patch
from .jobs import Fenced, OwnershipConflict
from .models import GraphSource, ProjectSettings, canonical
from .progress import label
from .provider import invalid_reason
from .sampling import SamplerService
from .schema_errors import explain
from .storage import RevisionConflict
from .validation import diagnose

DRIVER = "draft-assembly/1"


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
        if not {"draft", "generator"} & settings.endpoint_roles.keys():
            raise ValueError("Configure the Draft (or Generator) endpoint in Project Settings")
        if settings.generation.fast and "fast" in settings.endpoint_roles:
            reviews.review_schema(settings, "fast")
            if settings.generation.critical and "critical" in settings.endpoint_roles:
                reviews.review_schema(settings, "critical")
        instructions = deepcopy(instructions)
        retired = {"plan", "flow_yaml"} & instructions.keys()
        if retired:
            raise ValueError(
                "The step-by-step Flow and fixed plans were removed; runs use Draft and Assembly"
                " with the project's prompts (Settings). Remove: " + ", ".join(sorted(retired))
            )
        # Freeze concrete plans for ALL proposals before reserving attempts.
        proposals = [self.sampler.propose(**r, frozen=frozen) for r in requests]
        plans = {p["id"]: draft_assembly.plan(p["proposal"], settings) for p in proposals}
        return self.jobs.create_run(
            dict(instructions=instructions, sampler=frozen, plans=plans, driver=DRIVER),
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
        if j["config"]["config"].get("driver") != DRIVER:
            self.jobs.control(job, "pause")
            self.jobs.append_log(
                job, "This run was made by a retired generation method; start a new generation run."
            )
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
        if trajectory_hold.held(cp, epoch):
            return
        cp.update(_epoch=epoch, _revision=record.revision)
        if cp.get("held"):  # Resumed: retry it, with a fresh bounded correction.
            cp.pop("held")
            self._save(job, trajectory_hold.unflag(source), cp)
            return
        if self._parked(job, ident, cp, settings):
            return
        if cp["phase"] == "event":
            if cp.get("assembled"):
                self._after_generation(job, source, cp)
                return
            payload = draft_assembly.payload(cp, self._plan(job, ident), settings)
            tier = draft_assembly.role(settings, payload["operation"])
        elif cp["phase"] == assembly_fix.PHASE:
            payload = assembly_fix.payload(cp)
            if (
                len(canonical(payload["messages"]).encode())
                >= settings.generation.repair_context_bytes
            ):
                self._end_assembly_fix(job, source, cp, "context_limit")
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
        settings = self.settings_for(ticket.job_id)
        if assembly_fix.needed(source, settings):
            draft = source.metadata.get("draft", "")
            cp.update(
                phase=assembly_fix.PHASE,
                tier=draft_assembly.role(settings, "assembly"),
                errors=0,
                history=[],
            )
            cp["self_base"] = assembly_fix.base(source, settings, draft)
            self._save(ticket.job_id, source, cp, ticket=ticket)
            return
        self._to_review(ticket.job_id, source, broken, cp, ticket)

    def _end_assembly_fix(self, job, source, cp, reason, ticket=None):
        """Hand the graph on to review (or Fast repair if errors remain)."""
        settings = self.settings_for(job)
        data = source.to_dict()
        data["metadata"]["assembly_fix"] = assembly_fix.outcome(source, settings, cp, reason)
        source = GraphSource.model_validate(data)
        for key in ("self_base", "history"):
            cp.pop(key, None)
        cp.update(phase="event", errors=0)
        self.jobs.progress(
            job, f"Self-repair ended ({label(reason)}): trajectory={label(source.id)}"
        )
        self._to_review(job, source, assembly_fix.errors(source, settings), cp, ticket)

    def _assembly_fix_step(self, ticket, source, cp, body):
        settings = self.settings_for(ticket.job_id)
        if body["action"] == "give_up":
            self._end_assembly_fix(ticket.job_id, source, cp, "gave_up", ticket)
        elif body["action"] != "done":
            self._repair_action(ticket, source, cp, body)
        elif assembly_fix.errors(source, settings):
            cp["history"].extend(
                [
                    dict(role="assistant", content=canonical(body)),
                    assembly_fix.not_done(source, settings),
                ]
            )
            self._save(ticket.job_id, source, cp, ticket=ticket)
        else:
            self._end_assembly_fix(ticket.job_id, source, cp, "done", ticket)

    def _to_review(self, job, source, broken, cp, ticket=None):
        if not broken:
            cp["assembled"] = True
            self._save(job, source, cp, ticket=ticket)
            return
        settings = self.settings_for(job)
        data = source.to_dict()
        data["metadata"].update(
            stage=-1, generation_failure=dict(kind="assembled_with_structural_errors")
        )
        source = GraphSource.model_validate(data)
        cp.update(phase="repair", tier="fast", errors=0, history=[], round=1)
        cp["repair_base"] = reviews.payload(source, settings, "fast", "repair")["messages"]
        self._save(job, source, cp, ticket=ticket)

    def _hold(self, job, source, cp, reason, ticket=None):
        """Hold one trajectory for attention; the rest of the job keeps running."""
        trajectory_hold.mark(cp, ticket.epoch if ticket else cp["_epoch"], reason)
        self._save(job, trajectory_hold.flag(source, reason), cp, ticket=ticket)
        self.jobs.append_log(
            job,
            f"Trajectory {label(source.id)} held for attention: {reason} Other trajectories"
            " continue. Resume retries it; detach it to settle it as Unfinished.",
        )

    def _hold_owned(self, job, ident, epoch):
        """Hold a trajectory after a local error, from its last saved checkpoint."""
        record = self.store.read(ident)
        cp = self.jobs.checkpoint(job, ident)
        if cp is None:
            raise ValueError("No checkpoint to hold")
        cp.update(_epoch=epoch, _revision=record.revision)
        self._hold(job, record.source, cp, "A local error interrupted it; edits are kept.")

    def _parked(self, job, ident, cp, settings):
        """With a phase barrier, a run's reviews wait until every trajectory is assembled."""
        if cp.get("manual") or not settings.generation.phase_barrier:
            return False
        if not phase_barrier.waits(cp):
            return False
        if phase_barrier.phase(self.store, job) == phase_barrier.GENERATION:
            if phase_barrier.modes(self.store, job).get(ident) != "parked":
                phase_barrier.set_mode(self.store, job, ident, "parked")
                self.jobs.progress(job, f"Trajectory parked until reviews: {label(ident)}")
            return True
        if phase_barrier.modes(self.store, job).get(ident) == "parked":
            phase_barrier.set_mode(self.store, job, ident, "attached")
        return False

    def _barrier(self, job, epoch):
        """Pause the run once every trajectory is parked (or held) before its reviews."""
        j = self.jobs.inspect(job)
        if j["kind"] != "run" or j["state"] != "running":
            return
        if not self.settings_for(job).generation.phase_barrier:
            return
        if phase_barrier.phase(self.store, job) != phase_barrier.GENERATION:
            return
        if phase_barrier.pending_attempts(self.store, job):
            return
        owners = phase_barrier.modes(self.store, job)
        parked = [i for i, mode in owners.items() if mode == "parked"]
        held = [i for i in owners if trajectory_hold.held(self.jobs.checkpoint(job, i), epoch)]
        if not parked or len(parked) + len(held) < len(owners):
            return
        phase_barrier.advance(self.store, job)
        self.jobs.control(job, "pause")
        self.jobs.append_log(
            job,
            f"Phase barrier: Draft and Assembly finished for {len(parked)} trajectories"
            + (f" ({len(held)} held for attention)" if held else "")
            + ". Switch to the review models, then Resume to start Fast and Critical Review.",
        )

    def _pause_if_only_held(self, job, epoch):
        owners = self._owners(job)
        if not owners or self.jobs.inspect(job)["state"] != "running":
            return
        if all(trajectory_hold.held(self.jobs.checkpoint(job, i), epoch) for i in owners):
            self.jobs.control(job, "pause")
            self.jobs.append_log(
                job,
                f"Only held trajectories remain ({len(owners)}). Fix the cause, then Resume to"
                " retry them, or detach them.",
            )

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
            if cp["phase"] == assembly_fix.PHASE:
                self._end_assembly_fix(ticket.job_id, source, cp, "invalid_output", ticket)
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
                # No quality verdict exists. Keep the stage and hold this trajectory;
                # Resume gives it a fresh bounded correction.
                self._hold(
                    ticket.job_id,
                    source,
                    cp,
                    "Review/repair output stayed invalid after its corrections; no verdict"
                    " recorded.",
                    ticket,
                )
        else:
            if cp["phase"] in ("repair", assembly_fix.PHASE):
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
            if cp["phase"] == assembly_fix.PHASE:
                self._end_assembly_fix(ticket.job_id, source, cp, "context_limit", ticket)
            elif cp["phase"] == "repair":
                self._next_round(ticket.job_id, source, cp, ticket)
            else:
                self._hold(
                    ticket.job_id,
                    source,
                    cp,
                    (
                        f"Local context guard ({ticket.payload['operation']}): "
                        f"about {complete['local_budget']['input_tokens_estimate']} input tokens "
                        f"({complete['local_budget']['input_bytes']} bytes at "
                        f"{complete['local_budget']['bytes_per_token']} bytes/token) exceed the "
                        f"{complete['local_budget']['input_ceiling']}-token allowance. "
                        "No provider call made. Reduce prompt scope or change the endpoint budget; "
                        "active runs keep frozen settings."
                        if complete.get("local_budget")
                        else "Provider context limit; no quality verdict."
                    ),
                    ticket,
                )
            return
        body = complete.get("output")
        try:
            if complete.get("invalid_output"):
                raise ValueError(invalid_reason(complete))
            if ticket.payload["operation"] == draft_assembly.ASSEMBLE:
                body = spec_normalize.normalize(body)
            problem = explain(ticket.payload["schema"], body)
            if problem:
                raise ValueError(problem)
            if ticket.payload["operation"] == draft_assembly.ASSEMBLE:
                plan = self._plan(ticket.job_id, ticket.trajectory_id)
                assembled, broken = draft_assembly.assemble(source, body, plan, settings)
                if broken and not self._can_repair(settings):
                    raise ValueError(
                        "Structural errors; fix them: "
                        + "; ".join(f"{d.code}: {d.message}" for d in broken[:8])
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
        if cp["phase"] == assembly_fix.PHASE:
            self._assembly_fix_step(ticket, source, cp, body)
            return
        if ticket.payload["operation"] == draft_assembly.DRAFT:
            cp["draft"] = body["draft"]
            self._save(
                ticket.job_id, draft_assembly.with_draft(source, body["draft"]), cp, ticket=ticket
            )
            self.jobs.progress(ticket.job_id, f"Draft written: trajectory={label(source.id)}")
            return
        if ticket.payload["operation"] == draft_assembly.ASSEMBLE:
            self._assembled(ticket, assembled, broken, cp)
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
            # Unknown local wrapper/transport-adapter failures are resumable. Hold only
            # the trajectory involved; pause the job only if even that fails.
            if (
                "ticket" in locals()
                and ticket
                and self.jobs.inspect(ticket.job_id)["state"] == "running"
            ):
                try:
                    self._hold_owned(ticket.job_id, ticket.trajectory_id, ticket.epoch)
                except Exception:
                    self.jobs.control(ticket.job_id, "pause")
                    self.jobs.append_log(
                        ticket.job_id,
                        "Generation interrupted; completed commits retained. Manual Resume required.",
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
                try:
                    self._prepare(job, ident, epoch)
                except (Fenced, OwnershipConflict):
                    raise
                except Exception:
                    self._hold_owned(job, ident, epoch)
            self._pause_if_only_held(job, epoch)
            self._barrier(job, epoch)
            self.dispatch()
            await asyncio.sleep(0.025)
