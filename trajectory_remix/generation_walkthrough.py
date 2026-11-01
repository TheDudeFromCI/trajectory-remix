"""Request-scoped generation diagnostics. Never part of a saved checkpoint."""

from __future__ import annotations

import copy
import json
import os
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def json_equal(before, after):
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(json_equal(value, after[key]) for key, value in before.items())
    if isinstance(before, list):
        return len(before) == len(after) and all(json_equal(a, b) for a, b in zip(before, after))
    return before == after


def changes(before, after, path=''):
    """Lossless JSON-pointer changes, including the previous values."""
    if json_equal(before, after):
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
                # Inputs and raw outputs can contain JSON inside JSON strings.
                # Match every escaping depth that can fit in this value, longest
                # first so replacing a literal cannot damage an encoded match.
                representations = set(self.credentials)
                pending = list(self.credentials)
                while pending:
                    key = pending.pop()
                    for ascii_only in (False, True):
                        encoded = json.dumps(key, ensure_ascii=ascii_only)[1:-1]
                        if encoded not in representations and len(encoded) <= len(value):
                            representations.add(encoded)
                            pending.append(encoded)
                for key in sorted(representations, key=len, reverse=True):
                    value = value.replace(key, '[redacted]')
                return value
            if isinstance(value, dict):
                return {redact(key): redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        text = [Path(__file__).with_name('guides').joinpath('generation-walkthrough.md').read_text()]
        for entry in self.events:
            entry = redact(entry)
            payload = json.dumps(entry, ensure_ascii=False, indent=2)
            # A generated prompt or response may itself contain Markdown fences.
            fence = '`' * max(3, max((len(m) for m in re.findall(r'`+', payload)), default=0) + 1)
            path = f" ({entry['path']})" if entry.get('path') else ''
            text.append(f"## {entry['sequence']}. {entry['kind']}{path}\n\n{fence}json\n{payload}\n{fence}\n")
        return '\n'.join(text)
