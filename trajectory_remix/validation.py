"""Explicit, journaled validation of saved trajectories, separate from creation."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
import uuid
from collections import deque
from types import SimpleNamespace

import jsonschema

from .compiler import CompileError
from .dataset import evaluation_source
from .llm import SampleBuilder
from .model_session import ModelSession, EvaluationFailure
from .review import ReviewView, checklist_report, checklist_schema, compact, count
from .scenarios import Step, ReviewCheck
from .trajectory_review import fingerprint

CHUNK_REPORT = {'type': 'object', 'properties': {
    'valid': {'type': 'boolean'}, 'feedback': {'type': 'array', 'items': {'type': 'string'}},
    'context_requests': {'type': 'array', 'maxItems': 8, 'items': {'type': 'string'}}},
    'required': ['valid', 'feedback'], 'additionalProperties': False}


class ValidationRunner(ModelSession):
    def __init__(self, commands):
        self.commands = commands
        self.state, self.compiler, self.client = commands.state, commands.compiler, commands.client
        self.job = None
        self.logs = deque()
        self.status = {'running': False, 'phase': 'idle', 'active': [], 'completed': 0, 'total': 0}
        self.snapshot = None
        with self.state.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS validation_jobs (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)')
            row = db.execute('SELECT snapshot FROM validation_jobs ORDER BY rowid DESC LIMIT 1').fetchone()
        if row:
            self.snapshot = json.loads(row[0])
            self.status = self.snapshot['status']
            self.logs.extend(self.snapshot.get('logs', []))
            if self.status.get('running'):
                self.status.update(running=False, phase='interrupted', active=[])
                self.persist()

    @property
    def content_first(self):
        return bool(getattr(self, 'endpoint', {}).get('inference_tokenizer'))

    def elapsed_seconds(self):
        return time.monotonic() - self.started if hasattr(self, 'started') else 0

    async def gate(self):
        await self.resume_event.wait()

    def persist(self):
        if self.snapshot is None:
            return
        self.snapshot.update(status=copy.deepcopy(self.status), logs=list(self.logs))
        with self.state.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO validation_jobs VALUES (?,?)',
                       (self.snapshot['id'], self.clean(self.snapshot)))

    def log(self, message, level='info', step=None):
        self.logs.append({'time': time.time(), 'message': self.clean(message), 'level': level, 'step': step})
        self.persist()

    def start(self, sample_id, *, record=True, endpoint=None, resume=False):
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError('Select a saved trajectory to validate')
        if type(record) is not bool or type(resume) is not bool:
            raise ValueError('record and resume must be booleans')
        if endpoint is not None and (not isinstance(endpoint, str) or not endpoint):
            raise ValueError('endpoint must be a nonempty endpoint ID')
        if self.status.get('running'):
            raise ValueError('Trajectory validation is already running')
        owner = self.commands.runner
        if owner.status['running'] or owner.scenario_runner.status['running']:
            raise ValueError('Finish or stop generation before validating a trajectory')
        if getattr(owner, 'tokenizer_migration', None) and owner.tokenizer_migration.running:
            raise ValueError('Wait for tokenizer migration to finish')
        _, sample = self.commands._find(sample_id)
        self.original = copy.deepcopy(sample)
        self.compiler = self.commands.compiler
        checklist, origin = self.commands._review_checklist(sample)
        self.checklist_hash = fingerprint(checklist)
        self.config = copy.deepcopy(self.state['config'])
        self.endpoints = {e['id']: e for e in self.config['endpoints']}
        endpoint = endpoint or self.config.get('validation', {}).get('endpoint', 'validator')
        if endpoint not in self.endpoints:
            raise ValueError(f"Validation endpoint '{endpoint}' is not configured")
        self.endpoint = copy.deepcopy(self.endpoints[endpoint])
        # Models served by the same endpoint can share their exact tokenizer.
        if not self.endpoint.get('inference_tokenizer'):
            for candidate in self.endpoints.values():
                if (candidate.get('inference_tokenizer') and candidate.get('base_url') == self.endpoint.get('base_url')
                        and candidate['model'] == self.endpoint['model']):
                    self.endpoint['inference_tokenizer'] = candidate['inference_tokenizer']
                    break
        self.endpoints[endpoint] = self.endpoint
        inference_fingerprint = None
        if self.content_first:
            from .content_first import RequestBudget
            inference_fingerprint = RequestBudget(self.endpoint, self.config.get('content_first', {})).fingerprint
        source_hash = hashlib.sha256(compact([evaluation_source(sample), self.checklist_hash,
            self.compiler.fingerprint, self.compiler.tools.fingerprint, self.endpoint,
            inference_fingerprint, self.config.get('validation', {}), self.config.get('content_first', {})]).encode()).hexdigest()
        prior = self.snapshot
        if resume and (not prior or prior.get('source_hash') != source_hash or prior['status'].get('sample_id') != sample_id):
            raise ValueError('Trajectory, checklist, tokenizer or endpoint changed; start a fresh validation')
        self.snapshot = copy.deepcopy(prior) if resume else {'id': str(uuid.uuid4()), 'steps': {}, 'source_hash': source_hash,
            'checklist': checklist.model_dump(), 'origin': origin, 'sample': copy.deepcopy(sample)}
        self.status = {'running': True, 'phase': 'validating', 'sample_id': sample_id,
            'active': [], 'completed': 0, 'total': len(checklist.checks), 'pause_requested': False,
            'model_requests': 0, 'completed_model_seconds': 0, 'last_error': None, 'result': None}
        if not resume:
            self.logs.clear()
        self.started = time.monotonic()
        self.resume_event = asyncio.Event(); self.resume_event.set()
        self.limit = asyncio.Semaphore(1)
        self.first_prompts, self.first_prompt_paths = {}, {}
        self.builder = SampleBuilder(self.compiler, self.config)
        self.builder.sample = copy.deepcopy(sample)
        self.builder.next_tick = 0
        self.scenario = SimpleNamespace(prompts={}, flow=SimpleNamespace(schemas={}, generation_mode='content-first' if self.content_first else 'existing'))
        self.persist()
        self.job = asyncio.create_task(self.run(sample, checklist, endpoint, record))
        self.job.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return self.snapshot['id']

    async def stop(self):
        if self.job and not self.job.done():
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

    async def validate(self, sample_id, *, record=False, endpoint=None):
        self.start(sample_id, record=record, endpoint=endpoint)
        return await self.job

    async def run(self, sample, checklist, endpoint, record):
        report = None
        try:
            plan = self.compiler.compile(sample)
            self.builder.next_tick = plan['max_tick'] + 1
            context = {'roles': {}, 'steps': {}}
            inputs = {'original_requirements': sample.get('metadata', {}).get('concept', {}),
                      'roles': sample.get('metadata', {}).get('roles', {})}
            reports = {}
            for index, (name, check) in enumerate(checklist.checks.items()):
                prompt = f'check-{index}.md'
                self.scenario.prompts[prompt] = check.prompt
                path = 'validate.checks.' + name
                step = Step(uses='remix.validate', endpoint=endpoint, prompt=prompt, review_retries=2)
                if check.view:
                    definition = ReviewCheck(prompt=prompt, view=check.view, rules=check.rules,
                        inputs=['original_requirements', 'roles'],
                        max_input_tokens=self.config.get('validation', {}).get('max_input_tokens', 6000))
                    result, _ = await self.review_groups(step, definition, inputs, context, path,
                                                        sample, plan, self.snapshot['source_hash'])
                else:
                    # Legacy and custom prose checklists use the same bounded
                    # evidence protocol without inventing per-rule requirements.
                    definition = ReviewCheck(prompt=prompt, view='layout', inputs=['original_requirements', 'roles'],
                        max_input_tokens=self.config.get('validation', {}).get('max_input_tokens', 6000))
                    result, _ = await self.review_groups(step, definition, inputs, context, path,
                                                        sample, plan, self.snapshot['source_hash'])
                reports[name] = result
                self.status['completed'] = index + 1
                self.persist()
            structural = [f"{d['path']}: {d['message']}" for d in plan['diagnostics']
                          if d.get('code') in ('tool_not_shown', 'tools_in_system', 'direct_chat_generation')]
            delivery = self.chat_delivery_report(sample, plan)
            structural = list(dict.fromkeys([*structural, *delivery['feedback']]))
            report = {'valid': all(r['valid'] for r in reports.values()) and not structural,
                      'checks': reports, 'feedback': structural + [f'[{name}] {f}' for name, r in reports.items() for f in r['feedback']],
                      'diagnostics': plan['diagnostics'], 'delivery': delivery, 'recorded': False}
            if record:
                current_index, current = self.commands._find(sample['id'])
                current_checklist, _ = self.commands._review_checklist(current)
                if current != self.original or fingerprint(current_checklist) != self.checklist_hash or self.commands.compiler.fingerprint != self.compiler.fingerprint or self.commands.compiler.tools.fingerprint != self.compiler.tools.fingerprint:
                    raise ValueError('Trajectory or review requirements changed during validation; result was not recorded')
                saved = self.commands._save_review(current_index, copy.deepcopy(current),
                    'validated' if report['valid'] else 'rejected', self.endpoint['model'],
                    'Explicit trajectory validation against the saved original checklist and current custom guidance.',
                    self.state['revision'], report={k: v for k, v in report.items() if k != 'recorded'}, origin='model_checklist_review', checklist_hash=self.checklist_hash)
                report.update(recorded=True, validation_level=saved['validation_level'])
            self.status.update(phase='complete', result=report)
            return report
        except CompileError as error:
            report = {'valid': False, 'diagnostics': [error.diagnostic()], 'feedback': [str(error)], 'recorded': False}
            self.status.update(phase='complete', result=report)
            return report
        except asyncio.CancelledError:
            self.status.update(phase='cancelled', last_error='Validation cancelled; source and approval state unchanged')
            raise
        except Exception as error:
            self.status.update(phase='incomplete', last_error=self.clean(str(error)))
            raise
        finally:
            self.status.update(running=False, active=[])
            self.snapshot['elapsed_seconds'] = self.elapsed_seconds()
            self.persist()

    @staticmethod
    def chat_delivery_report(draft, plan, *, require_reply=None):
        user_paths = {f'replay[{tick}][{index}]' for tick, events in draft['replay'].items()
                      for index, event in enumerate(events) if event.get('op') == 'append'
                      and event.get('window') == 'chat' and event.get('source') == 'user'}
        feedback = [f"{d['path']}: {d['message']}" for d in plan['diagnostics']
                    if d.get('code') == 'direct_chat_generation']
        if require_reply is None:
            require_reply = bool(user_paths)
        latest = max((e['end'] for e in plan['events'] if e['path'] in user_paths), default=-1)
        if require_reply and not any(c['tool'] == 'chat' and not c.get('invisible') and not c.get('cancelled')
                and c.get('outcome', {}).get('ok') and c['arguments'].get('content', '').strip()
                and c['request_start'] >= latest for c in plan['tool_calls']):
            feedback.append('No successful, nonempty visible chat tool reply follows the latest user message.')
        return {'valid': not feedback, 'feedback': feedback}

    def failure_detail(self, error):
        return self.clean(error.message if isinstance(error, jsonschema.ValidationError) else str(error))[:256]

    async def review_response(
        self, step, payload, context, path, schema, prompt=None,
        *, definition=None, view=None, budget=None,
    ):
        """Retry invalid reviews without converting missing evidence into a verdict."""
        failures, rounds = 0, 0
        while True:
            if budget and "retry_feedback" in payload:
                for limit in (256, 128, 64):
                    if self.prompt_tokens(step, payload, context, schema, prompt, definition.rules) <= budget:
                        break
                    payload = {**payload, "retry_feedback": {"error": payload["retry_feedback"]["error"][:limit]}}
            try:
                result = await self.llm(
                    step, payload, context, path, schema, prompt,
                    max_input_tokens=budget,
                    rules=definition.rules if definition else None,
                )
                if definition and definition.rules:
                    result = checklist_report(result)
                requests = result.pop("context_requests", [])
                if not requests:
                    return result, payload
                if rounds == definition.context_rounds:
                    raise ValueError("Additional context still required after context_rounds; unseen evidence cannot be approved")
                extra = view.context(requests, payload)
                events = {u["id"]: u for u in payload["events"]}
                events.update({u["id"]: u for u in extra})
                expanded = {**payload, "events": list(events.values())}
                expanded.pop("retry_feedback", None)
                if not extra or self.prompt_tokens(
                    step, expanded, context, schema, prompt, definition.rules
                ) > budget:
                    raise ValueError("Requested evidence is unavailable, already supplied, or exceeds max_input_tokens; request specific missing unit IDs instead of an entire window")
                payload = expanded
                rounds += 1
            except (ValueError, jsonschema.ValidationError) as error:
                detail = self.failure_detail(error)
                if failures >= step.review_retries:
                    raise EvaluationFailure(f"{path}: {detail}") from error
                failures += 1
                self.log(f"Review recovery {failures}/{step.review_retries}: {detail}", "warning", path)
                payload = {**payload, "retry_feedback": {
                    "error": detail,
                    "instruction": "Correct the review/schema or context request. Use exact unit IDs, without a history: prefix. Review only supplied evidence; if essential evidence is still missing, request it instead of guessing a verdict.",
                }}

    async def review_groups(
        self, step, definition, inputs, context, path, draft, plan, source_hash
    ):
        schema = checklist_schema(definition.rules) if definition.rules else CHUNK_REPORT
        budget = definition.max_input_tokens
        endpoint = self.endpoints[step.endpoint]
        if endpoint.get("context_size"):
            budget = min(
                budget, endpoint["context_size"] - endpoint.get("max_tokens", 1024)
            )
        if self.content_first:
            from .content_first import RequestBudget
            request_budget = RequestBudget(endpoint, self.config.get('content_first', {}))
            budget = min(budget, request_budget.context - request_budget.completion - request_budget.reserve)
        if budget <= 0:
            raise ValueError(
                "Endpoint completion limit leaves no room for review input"
            )
        view = ReviewView(
            draft, plan, definition, self.compiler.tokenizer, tools=self.compiler.tools
        )
        fits = lambda payload: (
            self.prompt_tokens(
                step, payload, context, schema, definition.prompt, definition.rules
            )
            <= budget - (min(128, budget // 4) if step.review_retries else 0)
        )
        try:
            groups = list(view.groups(inputs, fits))
        except ValueError:
            # A large checklist may leave room for evidence but no recovery
            # reserve. Do not make an otherwise valid review impossible.
            groups = list(view.groups(inputs, lambda payload: self.prompt_tokens(
                step, payload, context, schema, definition.prompt, definition.rules
            ) <= budget))
        reports, failures = [], []
        for index, payload in enumerate(groups):
            group_path = f"{path}.groups.{index}"
            record = self.snapshot["steps"].setdefault(group_path, {})
            group_hash = hashlib.sha256(compact(payload).encode()).hexdigest()
            if (
                record.get("validation_source_hash") == source_hash
                and record.get("review_group_hash") == group_hash
            ):
                result = record["report"]
                payload = record["review_payload"]
            else:
                self.log(
                    {"review_group": index + 1, "groups": len(groups),
                     "units": len(payload["events"]), "input_budget": budget,
                     "instructions_tokens_estimated": count(
                         self.compiler.tokenizer,
                         self.render_prompt(step, payload, context, schema, definition.prompt, definition.rules)[0]),
                     "timeline_tokens_estimated": count(self.compiler.tokenizer, compact(payload["events"])),
                     "input_tokens_estimated": self.prompt_tokens(
                         step, payload, context, schema, definition.prompt, definition.rules)},
                    "info", group_path,
                )
                result, payload = await self.review_response(
                    step, payload, context, group_path, schema, definition.prompt,
                    definition=definition, view=view, budget=budget,
                )
                record.update(
                    validation_source_hash=source_hash,
                    review_group_hash=group_hash,
                    report=result,
                    review_payload=payload,
                )
                self.persist()
            reports.append(result)
            self.log(
                {"group": index + 1, **result},
                "success" if result["valid"] else "error",
                group_path,
            )
            if not result["valid"]:
                failures.append(
                    {
                        "check": path,
                        "group": index,
                        "review": payload,
                        "findings": result["feedback"]
                        or ["Review failed without details"],
                    }
                )
        findings = list(
            dict.fromkeys(
                finding for r in reports if not r["valid"] for finding in r["feedback"]
            )
        )
        return {
            "valid": all(r["valid"] for r in reports),
            "feedback": findings,
            "groups": len(groups),
            **(
                {"rule_results": [
                    {"group": index, "checks": r["checks"]}
                    for index, r in enumerate(reports)
                ]} if definition.rules else {}
            ),
        }, failures

