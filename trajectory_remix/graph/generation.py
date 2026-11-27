"""Durable Draft, Assembly, review and Assistant driver over the existing FIFO services."""

import asyncio
import json
from copy import deepcopy

from . import (
    assistant,
    assistant_memory,
    assistant_proposals,
    authoring_session,
    draft_assembly,
    phase_barrier,
    reviews,
    signoffs,
    trajectory_hold,
)
from .draft_reply import draft_text
from .failure_reason import local_failure, log_local_error
from .jobs import Fenced, OwnershipConflict
from .models import GraphSource
from .progress import label
from .provider import assistant_message, invalid_reason
from .sampling import SamplerService
from .storage import RevisionConflict

DRIVER = draft_assembly.DRIVER


def context_reason(ticket, complete):
    """Why a Draft request was held for its context allowance."""
    budget = complete.get("local_budget")
    if not budget:
        return "Provider context limit; no quality verdict."
    return (
        f"Local context guard ({ticket.payload['operation']}): "
        f"about {budget['input_tokens_estimate']} input tokens "
        f"({budget['input_bytes']} bytes at {budget['bytes_per_token']} bytes/token) exceed the "
        f"{budget['input_ceiling']}-token allowance. No provider call made. Reduce prompt scope "
        "or change the endpoint budget; active runs keep frozen settings."
    )


class GenerationService:
    def __init__(self, jobs, sampler, bulk, transport):
        self.jobs, self.store, self.sampler, self.bulk = jobs, jobs.store, sampler, bulk
        self.transport = transport
        self.calls = set()
        self.comments = None  # The studio's CommentService, for assistant sessions.

    def settings_for(self, job):
        config = self.jobs.inspect(job)["config"]
        return self.store.thaw(
            config["settings"] if "settings" in config else config["options"]["settings"]
        )

    @staticmethod
    def _driver(job):
        config = job["config"]
        return config.get("config", config.get("options", {})).get("driver")

    def start(self, requests, instructions, barriers=None):
        barriers = phase_barrier.validate(barriers)
        frozen = self.sampler.editor()
        settings_record = self.store.settings(frozen["settings_revision"])
        settings = settings_record.settings
        if "draft" not in settings.endpoint_roles:
            raise ValueError("Configure the Draft endpoint in Project Settings")
        for tier in reviews.automatic_tiers(settings.generation, settings.endpoint_roles):
            reviews.require_checklist(settings, tier)
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
            dict(
                instructions=instructions,
                sampler=frozen,
                plans=plans,
                driver=DRIVER,
                barriers=barriers,
            ),
            proposals,
            settings_record=settings_record,
        )

    def start_review(self, query, *, mode="standard", trajectory=None, expected_revision=None):
        if mode not in {"standard", "force_fast", "force_critical"}:
            raise ValueError("Invalid review mode")
        snapshot = self.store.settings()
        settings = snapshot.settings
        for tier in ("fast", "critical"):
            if tier in settings.endpoint_roles:
                reviews.require_checklist(settings, tier)
        options = dict(
            mode=mode,
            settings=self.store.frozen(settings),
            settings_revision=snapshot.revision,
            driver=DRIVER,
        )
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

    def start_assistant(self, trajectory, expected_revision, comments):
        """Queue an assistant session on one trajectory's open comments."""
        snapshot = self.store.settings()
        settings = snapshot.settings
        if assistant.ROLE not in settings.endpoint_roles:
            raise ValueError("Assign the Assistant endpoint role in Project Settings first")
        frozen = assistant.frozen(comments.for_trajectory(trajectory, "open"))
        if not frozen:
            raise ValueError("This trajectory has no open comments to send")
        options = dict(
            settings=self.store.frozen(settings),
            settings_revision=snapshot.revision,
            comments=frozen,
            conversation=assistant_memory.load(self.store, trajectory),
        )
        record = self.store.read(trajectory)
        self.store._check_revision(trajectory, expected_revision, record)
        assistant_proposals.rebase(self.store, record)  # Before taking the lock: own connection.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.store._read(db, trajectory)
            self.store._check_revision(trajectory, expected_revision, record)
            job = self.jobs._create(
                db, "bulk", dict(query={}, action=assistant.ACTION, options=options), 1
            )
            db.execute(
                "INSERT INTO graph_bulk_items VALUES(?,0,?,?,'pending',NULL,NULL)",
                (job, trajectory, record.revision),
            )
            return job

    def _operation(self, job, source, settings):
        """(endpoint role, phase) for a manual job's trajectory, or None to skip it."""
        config = job["config"]
        if config.get("action") == assistant.ACTION:
            return assistant.ROLE, assistant.PHASE
        return reviews.manual_operation(
            source.metadata.get("stage", -3), config["options"]["mode"], settings
        )

    def _assist_session(self, job, record, settings):
        """Continue the kept conversation when it fits the budget, else start afresh. The
        session edits a copy: the pending proposal, else the trajectory as it is."""
        source, options = record.source, job["config"]["options"]
        prior = options.get("conversation")
        session = assistant.begin(source, settings, options["comments"], prior)
        if prior and assistant.over_context_budget(settings, session["messages"]):
            session = assistant.begin(source, settings, options["comments"])
        session.update(
            working=assistant_proposals.working(self.store, source.id, source),
            base_revision=record.revision,
        )
        return session

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
        if accepted.get("repeated_exchange_limit"):
            endpoint = label(cp["exchange_limit_endpoint"])
            exchanges = cp["authoring_session"]["exchanges"]
            self.jobs.progress(
                job,
                f"Endpoint {endpoint} reached the {exchanges}-exchange limit twice in this job. "
                "Consider increasing Maximum exchanges per pass in Review Settings for new runs.",
                "warning",
            )
        if outcome is not None:
            self.jobs.progress(
                job,
                f"Trajectory settled: {label(source.id)} outcome={label(outcome)} events={len(source.events)}",
                {"pass": "success", "fail": "error"}.get(outcome, "info"),
            )
        return accepted

    def _phase(self, job, source, cp, phase, tier=None, *, outcome=None):
        cp.update(phase=phase, tier=tier, errors=0)
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
        tiers = reviews.automatic_tiers(settings.generation, settings.endpoint_roles)
        if tiers:
            self._phase(job, source, cp, "review", tiers[0])
        else:
            self._phase(job, source, cp, "done", outcome="pass")

    def _retired(self, job, j, message):
        """Pause a job made for an earlier pipeline, whose phases no longer exist."""
        if self._driver(j) == DRIVER:
            return False
        self.jobs.control(job, "pause")
        self.jobs.append_log(job, message, level="error")
        return True

    def _admit(self, job, epoch=None):
        j = self.jobs.inspect(job)
        if epoch is not None and j["epoch"] != epoch:
            raise Fenced("Worker epoch changed")
        # Only this handoff's explicitly prepared runs have model access. Generic
        # /runs and historical sampler snapshots cannot silently become live jobs.
        if self._retired(
            job, j, "This run was made by a retired generation method; start a new generation run."
        ):
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
        if j["config"].get("action") == "review_repair" and self._retired(
            job, j, "This review was queued for a retired review pipeline; start a new review."
        ):
            return
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
                        settings = self.settings_for(job)
                        operation = self._operation(j, record.source, settings)
                        if not operation or operation[0] not in settings.endpoint_roles:
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
        j = self.jobs.inspect(job)
        epoch = j["epoch"] if epoch is None else epoch
        if cp is None:  # Crash immediately after atomic start_attempt is safe.
            if j["kind"] == "bulk":
                with self.store.connect() as db:
                    item = db.execute(
                        "SELECT position FROM graph_bulk_items WHERE job_id=? AND trajectory_id=?",
                        (job, ident),
                    ).fetchone()
                operation = self._operation(j, source, settings)
                cp = dict(
                    serial=0,
                    phase=operation[1],
                    tier=operation[0],
                    errors=0,
                    manual=True,
                    bulk_position=item[0],
                )
            else:
                cp = dict(serial=0, phase="event", index=0, errors=0, manual=False)
        if trajectory_hold.held(cp, epoch):
            return
        cp.update(_epoch=epoch, _revision=record.revision)
        if j["kind"] == "run" and self._waiting_for_slot(j, ident, cp, epoch):
            return
        if cp.get("held"):  # Resumed: retry it, with a fresh bounded correction.
            cp.pop("held")
            self._save(job, trajectory_hold.unflag(source), cp)
            return
        phase = cp["phase"]
        if phase == "event" and cp.get("assembled"):
            self._after_generation(job, source, cp)
            return
        if phase == "review" or (phase == "event" and "draft" in cp):
            session = cp.get("authoring_session")
            if session is None:
                plan = self._plan(job, ident) if phase == "event" else None
                session = authoring_session.begin(
                    source, settings, "assembly" if plan else phase, cp.get("tier"), plan=plan
                )
                cp["authoring_session"] = session
            tier = draft_assembly.role(settings, "assembly") if phase == "event" else cp["tier"]
            payload = authoring_session.payload(session)
        elif phase == "event":
            payload = draft_assembly.payload(cp, self._plan(job, ident))
            tier = draft_assembly.role(settings, draft_assembly.DRAFT)
        elif phase == assistant.PHASE:
            tier = cp["tier"]
            if "authoring_session" not in cp:
                cp["authoring_session"] = self._assist_session(j, record, settings)
            payload = assistant.payload(cp["authoring_session"])
            if assistant.over_context_budget(settings, payload["messages"]):
                self._assist_end(job, source, cp, "context_limit")
                return
        else:
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
            "warning" if paused else "info",
        )

    def _plan(self, job, ident):
        with self.store.connect() as db:
            proposal = json.loads(
                db.execute(
                    "SELECT proposal FROM graph_attempts WHERE trajectory_id=?", (ident,)
                ).fetchone()[0]
            )
        return self.jobs.inspect(job)["config"]["config"]["plans"][proposal["id"]]

    def _hold(self, job, source, cp, reason, ticket=None):
        """Hold one trajectory for attention; the rest of the job keeps running."""
        trajectory_hold.mark(cp, ticket.epoch if ticket else cp["_epoch"], reason)
        self._save(job, trajectory_hold.flag(source, reason), cp, ticket=ticket)
        self.jobs.append_log(
            job,
            f"Trajectory {label(source.id)} held for attention: {reason} Other trajectories"
            " continue. Resume retries it; detach it to settle it as Unfinished.",
            level="warning",
        )

    def _hold_owned(self, job, ident, epoch, error=None):
        """Hold a trajectory after a local error, from its last saved checkpoint."""
        record = self.store.read(ident)
        cp = self.jobs.checkpoint(job, ident)
        if cp is None:
            raise ValueError("No checkpoint to hold")
        cp.update(_epoch=epoch, _revision=record.revision)
        cause = f" ({local_failure(error)}; traceback in the server output)" if error else ""
        self._hold(job, record.source, cp, f"A local error interrupted it{cause}; edits are kept.")

    def _waiting_for_slot(self, j, ident, cp, epoch):
        """Wait at a closed gate, or until a released attempt has an active slot."""
        enabled = phase_barrier.configured(j)
        if not enabled and not cp.get("held"):
            return False
        job = j["id"]
        mode = phase_barrier.mode(self.store, job, ident)
        gate = phase_barrier.gate(cp)
        if (
            gate in enabled
            and not cp.get("held")
            and gate in phase_barrier.closed(enabled, phase_barrier.phase(self.store, job))
        ):
            if mode == "attached" and self.jobs.park_attempt(job, epoch, ident):
                self.jobs.progress(job, f"Trajectory parked before {gate}: {label(ident)}")
            return True
        return mode in {"parked", "held"} and not self.jobs.activate_attempt(job, epoch, ident)

    def _barrier(self, job, epoch):
        """Pause the run once every trajectory is parked (or held) at a barrier."""
        j = self.jobs.inspect(job)
        enabled = phase_barrier.configured(j)
        if j["state"] != "running" or not enabled:
            return
        if phase_barrier.pending_attempts(self.store, job):
            return
        closed = phase_barrier.closed(enabled, phase_barrier.phase(self.store, job))
        if not closed:
            return
        owners = phase_barrier.modes(self.store, job)
        checkpoints = {i: self.jobs.checkpoint(job, i) for i in owners}
        parked = [
            gate
            for i, mode in owners.items()
            if mode == "parked" and (gate := phase_barrier.gate(checkpoints[i])) in closed
        ]
        held = [i for i, cp in checkpoints.items() if trajectory_hold.held(cp, epoch)]
        if not parked or len(parked) + len(held) < len(owners):
            return
        gate = min(parked, key=phase_barrier.GATES.index)
        phase_barrier.advance(self.store, job, phase_barrier.GATES.index(gate) + 1)
        self.jobs.control(job, "pause")
        self.jobs.append_log(
            job,
            f"Phase barrier before {gate}: {len(parked)} trajectories are waiting"
            + (f" ({len(held)} held for attention)" if held else "")
            + f". Switch to the {gate} model if you need to, then Resume to continue.",
            level="warning",
        )

    def _pause_if_only_held(self, job, epoch):
        owners = self._owners(job)
        j = self.jobs.inspect(job)
        if not owners or j["state"] != "running":
            return
        if all(trajectory_hold.held(self.jobs.checkpoint(job, i), epoch) for i in owners):
            if j["run_id"] and phase_barrier.pending_attempts(self.store, job):
                return
            self.jobs.control(job, "pause")
            self.jobs.append_log(
                job,
                f"Only held trajectories remain ({len(owners)}). Fix the cause, then Resume to"
                " retry them, or detach them.",
                level="warning",
            )

    def _invalid(self, ticket, source, cp):
        """An unusable Draft reply: correct it, up to format_retries, then fail the attempt."""
        settings = self.settings_for(ticket.job_id)
        cp["errors"] += 1
        self.jobs.progress(
            ticket.job_id,
            f"Unusable Draft reply; correction {cp['errors']}/{settings.generation.format_retries}: {label(ticket.payload.get('operation', cp['phase']))} trajectory={label(source.id)}",
            "warning",
        )
        if cp["errors"] <= settings.generation.format_retries:
            self._save(ticket.job_id, source, cp, ticket=ticket)
            return
        data = source.to_dict()
        data["metadata"]["stage"] = -1
        data["metadata"]["generation_failure"] = {
            "kind": "invalid_provider_output",
            "event_index": cp["index"],
        }
        cp["phase"] = "done"
        self._save(
            ticket.job_id, GraphSource.model_validate(data), cp, ticket=ticket, outcome="fail"
        )

    def consume(self, ticket):
        complete = self.jobs.result(ticket)
        cp = deepcopy(ticket.payload["checkpoint"])
        record = self.store.read(ticket.trajectory_id)
        source = record.source
        if record.revision != ticket.payload["source_revision"]:
            raise Fenced("Source changed while request was in progress")
        settings = self.settings_for(ticket.job_id)
        if cp["phase"] == assistant.PHASE:
            self._consume_assist(ticket, source, cp, complete)
        elif "tools" in ticket.payload:
            self._consume_native(ticket, record, settings, source, cp, complete)
        elif complete.get("context_limit"):
            self._hold(ticket.job_id, source, cp, context_reason(ticket, complete), ticket)
        else:
            self._consume_draft(ticket, source, cp, complete)

    def _consume_draft(self, ticket, source, cp, complete):
        try:
            if complete.get("invalid_output"):
                raise ValueError(invalid_reason(complete))
            draft = draft_text(complete)
        except ValueError as error:
            cp["flow_error"] = str(error)[:2000]
            self._invalid(ticket, source, cp)
            return
        cp["errors"] = 0
        cp["draft"] = draft
        self._save(ticket.job_id, draft_assembly.with_draft(source, draft), cp, ticket=ticket)
        self.jobs.progress(ticket.job_id, f"Draft written: trajectory={label(source.id)}")

    def _consume_native(self, ticket, record, settings, source, cp, complete):
        session = cp["authoring_session"]
        if complete.get("context_limit"):
            budget = complete.get("local_budget")
            detail = (
                f"Estimated input of {budget['input_tokens_estimate']} tokens exceeds the "
                f"{budget['input_ceiling']}-token allowance."
                if budget
                else "The provider reported a context limit."
            )
            self._authoring_failure(ticket, source, cp, "context_limit", detail=detail)
            return
        try:
            if complete.get("invalid_output"):
                raise ValueError("The provider returned an incomplete native reply")
            message = assistant_message(complete.get("message"))
        except ValueError:
            session["exchanges"] += 1
            cp["errors"] += 1
            if session["exchanges"] >= settings.generation.max_authoring_exchanges:
                self._authoring_failure(ticket, source, cp, "exchange_limit")
                return
            if cp["errors"] > settings.generation.format_retries:
                if session["phase"] == "review":
                    self._hold(
                        ticket.job_id,
                        source,
                        cp,
                        "Review output stayed invalid; no verdict recorded.",
                        ticket,
                    )
                else:
                    self._authoring_failure(ticket, source, cp, "invalid_output")
                return
            session["messages"].append(
                dict(
                    role="user",
                    content="The prior reply was incomplete. Return complete native tool calls or signoff.",
                )
            )
            terminal = None
        else:
            cp["errors"] = 0
            source, terminal = authoring_session.step(source, settings, session, message)
        if terminal is None:
            if session["exchanges"] >= settings.generation.max_authoring_exchanges:
                self._authoring_failure(ticket, source, cp, "exchange_limit")
            else:
                self._save(ticket.job_id, source, cp, ticket=ticket)
            return
        result, evidence = terminal
        if result == "unfeasible" and session["phase"] == "assembly":
            self._authoring_failure(ticket, source, cp, result, detail=evidence["summary"])
            return
        cp.pop("authoring_session")
        if session["phase"] == "assembly":
            live = self.jobs.inspect(ticket.job_id)["config"]
            source = signoffs.record(
                source,
                settings,
                evidence,
                operation="assembly",
                verdict=result,
                draft_visible=session["draft_visible"],
                provenance=dict(
                    source_revision=record.revision
                    + (record.intrinsic_hash != source.intrinsic_hash),
                    settings_revision=live["settings_revision"],
                    endpoint=ticket.endpoint,
                    model=complete.get("model"),
                    requested_model=settings.endpoints[ticket.endpoint].model,
                    prompt_version=session["prompt_version"],
                    exchanges=session["exchanges"],
                ),
            )
            self.jobs.progress(
                ticket.job_id,
                f"Graph assembled: trajectory={label(source.id)} events={len(source.events)}",
            )
            cp["assembled"] = True
            self._save(ticket.job_id, source, cp, ticket=ticket)
        else:
            # A review that cannot make the trajectory pass signs off as unfeasible: a failure.
            failed = result == "unfeasible"
            evidence = evidence | dict(unfeasible=True) if failed else evidence
            self._finish_review(
                ticket, record, settings, source, cp, complete, evidence, session, failed
            )

    def _authoring_failure(self, ticket, source, cp, reason, detail=None):
        session = cp["authoring_session"]
        phase = session["phase"]
        data = source.to_dict()
        metadata = data.setdefault("metadata", {})
        if phase != "review":
            metadata["stage"] = -3
        key = f"{phase}_outcomes"
        outcomes = metadata[key] if isinstance(metadata.get(key), list) else []
        outcome = dict(
            tier=session["tier"],
            outcome=reason,
            source_hash=source.intrinsic_hash,
            exchanges=session["exchanges"],
        )
        if detail:
            outcome["reason"] = detail
        metadata[key] = outcomes + [outcome]
        self.jobs.progress(
            ticket.job_id,
            f"{label(phase)} ended: {label(reason)} trajectory={label(source.id)}",
            "warning",
        )
        if reason == "exchange_limit":
            cp["exchange_limit_endpoint"] = ticket.endpoint
        cp["phase"] = "done"
        self._save(
            ticket.job_id, GraphSource.model_validate(data), cp, ticket=ticket, outcome="fail"
        )

    def _finish_review(
        self, ticket, record, settings, source, cp, complete, evidence, session, failed
    ):
        config = self.jobs.inspect(ticket.job_id)["config"]
        provenance = signoffs.stamp(
            source, settings, draft_visible=session["draft_visible"], binds_review_option=True
        ) | dict(
            settings_revision=config.get(
                "settings_revision", config.get("options", {}).get("settings_revision")
            ),
            exchanges=session["exchanges"],
        )
        source, result = reviews.signoff(
            source,
            record,
            settings,
            cp["tier"],
            evidence,
            result="fail" if failed else "pass",
            endpoint=ticket.endpoint,
            model=complete.get("model"),
            requested_model=settings.endpoints[ticket.endpoint].model,
            provenance=provenance,
            prompt_version=session["prompt_version"],
        )
        self.jobs.progress(
            ticket.job_id,
            f"{label(cp['tier'])} review verdict: {label(result)} trajectory={label(source.id)}",
            "success" if result == "pass" else "warning",
        )
        next_critical = (
            not cp["manual"]
            and result == "pass"
            and cp["tier"] == "fast"
            and "critical" in reviews.automatic_tiers(settings.generation, settings.endpoint_roles)
        )
        if next_critical:
            cp.update(tier="critical", errors=0)
            self._save(ticket.job_id, source, cp, ticket=ticket)
        else:
            cp["phase"] = "done"
            self._save(ticket.job_id, source, cp, ticket=ticket, outcome=result)

    def _consume_assist(self, ticket, source, cp, complete):
        """One native Assistant exchange: edits, replies, and the end of the session."""
        settings = self.settings_for(ticket.job_id)
        session = cp["authoring_session"]
        if complete.get("context_limit"):
            self._assist_end(ticket.job_id, source, cp, "context_limit", ticket)
            return
        try:
            if complete.get("invalid_output"):
                raise ValueError("The provider returned an incomplete native reply")
            message = assistant_message(complete.get("message"))
        except ValueError:
            session["exchanges"] += 1
            cp["errors"] += 1
            if cp["errors"] > settings.generation.format_retries:
                self._assist_end(ticket.job_id, source, cp, "invalid_output", ticket)
                return
            session["messages"].append(
                dict(
                    role="user", content="The prior reply was incomplete. Return native tool calls."
                )
            )
            finished = False
        else:
            cp["errors"] = 0
            self._log_assist_tools(ticket.job_id, message)
            # A session queued before proposals existed starts its copy here.
            session.setdefault("working", source.to_dict())
            session.setdefault("base_revision", ticket.payload["source_revision"])
            working = GraphSource.model_validate(session["working"])
            working, finished = assistant.step(working, settings, session, message)
            session["working"] = working.to_dict()
        if finished:
            self._assist_finish(ticket, source, cp)
        elif session["exchanges"] >= settings.generation.max_authoring_exchanges:
            self._assist_end(ticket.job_id, source, cp, "exchange_limit", ticket)
        else:
            self._save(ticket.job_id, source, cp, ticket=ticket)

    def _log_assist_tools(self, job, message):
        """One line per tool call; the editor reads them to show "Running <tool>"."""
        for call in message.get("tool_calls", []):
            self.jobs.progress(job, f"Assistant tool: {call['function']['name']}")

    def _assist_finish(self, ticket, source, cp):
        session = cp.pop("authoring_session")
        proposed = self._keep_proposal(source, session)
        addressed = assistant.apply(self.comments, session["replies"])
        assistant_memory.save(self.store, source.id, session["messages"])
        cp["phase"] = "done"
        self._save(ticket.job_id, source, cp, ticket=ticket, outcome="pass")
        answered = len(session["replies"])
        self.jobs.progress(
            ticket.job_id,
            f"Assistant finished: trajectory={label(source.id)} addressed={addressed}"
            f" replied={answered - addressed}; "
            + (
                "proposed changes await your review in Comments"
                if proposed
                else "addressed comments await your review"
            ),
            "success",
        )

    def _keep_proposal(self, source, session):
        """Keep the session's working copy as the trajectory's proposal (never the source)."""
        if "working" not in session:  # A session started before proposals edited the source.
            return False
        addressed = [ident for ident, answer in session["replies"].items() if answer["done"]]
        return assistant_proposals.keep(
            self.store, source.id, session["base_revision"], session["working"], addressed
        )

    def _assist_end(self, job, source, cp, reason, ticket=None):
        """End an assistant session without answering; edits so far stay proposed."""
        if reason == "context_limit":  # Too long to continue: the next send starts afresh.
            assistant_memory.forget(self.store, source.id)
        session = cp.pop("authoring_session", None)
        if session:
            self._keep_proposal(source, session)
            assistant.fail(self.comments, session, reason)
        cp["phase"] = "done"
        self._save(job, source, cp, ticket=ticket, outcome="fail")
        self.jobs.progress(
            job,
            f"Assistant stopped ({label(reason)}): trajectory={label(source.id)};"
            " edits so far are kept as proposed changes and the comments stay open.",
            "warning",
        )

    async def _call(self, endpoint):
        try:
            ticket = await self.jobs.execute_one(endpoint, self.transport)
            if ticket:
                self.consume(ticket)
        except (Fenced, asyncio.CancelledError):
            pass
        except Exception as error:
            # Unknown local wrapper/transport-adapter failures are resumable. Hold only
            # the trajectory involved; pause the job only if even that fails.
            if "ticket" in locals() and ticket:
                log_local_error(error, "Generation step", ticket.job_id)
            if (
                "ticket" in locals()
                and ticket
                and self.jobs.inspect(ticket.job_id)["state"] == "running"
            ):
                try:
                    self._hold_owned(ticket.job_id, ticket.trajectory_id, ticket.epoch, error)
                except Exception:
                    self.jobs.control(ticket.job_id, "pause")
                    self.jobs.append_log(
                        ticket.job_id,
                        f"Generation interrupted by {local_failure(error)}; completed commits"
                        " retained. The traceback is in the server output. Manual Resume required.",
                        level="error",
                    )

    def dispatch(self):
        # Claims remain the sole ordering/cap authority. Each task is one queue
        # item, transport retries remain inside execute_one, never at the tail.
        self.jobs.reconnect_due()
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
                except Exception as error:
                    log_local_error(error, "Preparing trajectory " + label(ident), job)
                    self._hold_owned(job, ident, epoch, error)
            self._pause_if_only_held(job, epoch)
            self._barrier(job, epoch)
            self.dispatch()
            await asyncio.sleep(0.025)
