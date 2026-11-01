"""Request-scoped generation diagnostics. Never part of a saved checkpoint."""

from __future__ import annotations

import copy
import json
import os
import re
from contextlib import contextmanager
from datetime import UTC, datetime


def changes(before, after, path=''):
    """Lossless JSON-pointer changes, including the previous values."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if key not in before:
                result.append({'op': 'add', 'path': pointer, 'after': after[key]})
            elif key not in after:
                result.append({'op': 'remove', 'path': pointer, 'before': before[key]})
            else:
                result.extend(changes(before[key], after[key], pointer))
        return result
    return [{'op': 'replace', 'path': path, 'before': before, 'after': after}]


class GenerationWalkthrough:
    def __init__(self, runner):
        self.runner = runner
        self.events = []
        self.last_compilation = None
        self.closed = False
        self.credentials = set()
        self.remember_credentials(runner.state['config'])

    def remember_credentials(self, config):
        for endpoint in config['endpoints']:
            key = endpoint.get('api_key') or os.getenv(endpoint.get('api_key_env', ''), '')
            if key:
                self.credentials.add(key)

    def event(self, kind, path=None, **details):
        if self.closed:
            return {'sequence': None}
        entry = {'sequence': len(self.events) + 1, 'time': datetime.now(UTC).isoformat(),
                 'kind': kind, 'path': path, **copy.deepcopy(details)}
        self.events.append(entry)
        return entry

    def close(self):
        self.closed = True
        self.events.clear()
        self.credentials.clear()

    def workspace(self):
        builder = getattr(self.runner, 'builder', None)
        if builder is None:
            return {}
        sample = copy.deepcopy(builder.sample)
        # These are ordinary durable generation records, not workspace content.
        for key in ('generation_record', 'generation_metrics'):
            sample.get('metadata', {}).pop(key, None)
        return {'sample': sample, 'next_tick': builder.next_tick, 'serial': builder.serial,
                'last_compilation_event': self.last_compilation}

    @contextmanager
    def scope(self, kind, path=None, **details):
        before = self.workspace()
        started = self.event(kind + ' started', path, workspace_before=before, **details)
        result = {}
        try:
            yield result
        except BaseException as error:
            result.update(error={'type': type(error).__name__, 'detail': str(error)})
            raise
        finally:
            if not self.closed:
                self.event(kind + ' finished', path, started_event=started['sequence'],
                           workspace_changes=changes(before, self.workspace()), **result)

    def observe_plan(self, plan):
        # Observe existing compiler results; never replay tools for diagnostics.
        sandbox = plan['sandbox']
        previous = sandbox['initial']
        timeline = []
        for tick, state in sandbox['snapshots'].items():
            timeline.append({'tick': tick, 'changes': changes(previous, state), 'state': state})
            previous = state
        calls = [{key: value for key, value in call.items() if key in {
            'path', 'tool', 'arguments', 'outcome', 'request_start', 'start', 'end',
            'result_end', 'invisible', 'window', 'request-id',
        }} for call in plan['tool_calls']]
        entry = self.event('Compiler result', sample_id=plan['sample_id'],
                           max_tick=plan['max_tick'], diagnostics=plan['diagnostics'],
                           windows=plan['windows'], tool_calls=calls,
                           sandbox_initial=sandbox['initial'], sandbox_timeline=timeline)
        self.last_compilation = entry['sequence']

    def attempt_started(self):
        runner = self.runner
        self.remember_credentials(runner.config)
        self.event('Generation attempt', scenario=runner.scenario.name,
                   job_id=runner.snapshot['id'], batch=runner.status['batch'],
                   flow=runner.snapshot['flow'], conditions=runner.status['conditions'],
                   context=runner.context, prompt_sources=runner.scenario.prompts,
                   saved_review_checklist=runner.builder.sample['metadata']['review_checklist'],
                   settings={key: runner.config.get(key) for key in (
                       'single_generation', 'content_first', 'clock', 'max_tool_calls',
                   )}, max_parallel=runner.snapshot['max_parallel'],
                   workspace=self.workspace())

    def render(self):
        self.remember_credentials(self.runner.state['config'])

        def redact(value):
            if isinstance(value, str):
                for key in sorted(self.credentials, key=len, reverse=True):
                    value = value.replace(key, '[redacted]')
                return value
            if isinstance(value, dict):
                return {redact(key): redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        text = [
            '# Scenario generation walkthrough\n',
            'This report follows the normal generation pipeline. Drafts, checkpoints and ordinary '
            'generation records are saved normally; this walkthrough is request-scoped and is '
            'never stored in the database.\n',
            'Events are ordered by observation time. Start/finish event IDs identify overlapping '
            'requests and nested steps. Step definitions include dependencies, conditions, retries '
            'and fallbacks. Model requests contain the exact rendered messages and original JSON '
            'schema, plus the provider decoding schema and local constraints. Prompt sources are '
            'listed separately: a source or saved review checklist is only shown to a model if it '
            'appears in that request’s messages.\n',
            'Generation enforces request budgets when an inference tokenizer is available, JSON '
            'parsing/schema checks and compiler/tool contracts. Semantic review is deferred to '
            'Validate Trajectory; saved review checklists are not automatically enforced during '
            'creation. Budget skips, failures and compiler diagnostics appear below.\n',
            'Model responses do not apply workspace edits themselves. Subsequent remix steps '
            'construct the trajectory and execute simulated tools. Workspace changes use JSON '
            'pointers with before/after values. Compiler results include each tool call and the '
            'sandbox state and changes at every recorded execution tick; simultaneous changes '
            'share a tick. Compiler results may include earlier calls replayed or cached by the '
            'normal pipeline. No extra tool execution is performed for this report.\n',
            'Reasoning is included only when returned by the provider. Missing reasoning is '
            'marked unavailable. Token metrics retain their reported/estimated source. '
            'Configured endpoint credentials are redacted.\n',
        ]
        for entry in self.events:
            entry = redact(entry)
            payload = json.dumps(entry, ensure_ascii=False, indent=2)
            # A generated prompt or response may itself contain Markdown fences.
            fence = '`' * max(3, max((len(m) for m in re.findall(r'`+', payload)), default=0) + 1)
            path = f" ({entry['path']})" if entry.get('path') else ''
            text.append(f"## {entry['sequence']}. {entry['kind']}{path}\n\n{fence}json\n{payload}\n{fence}\n")
        return '\n'.join(text)
