"""Request-scoped generation diagnostics. Never part of a saved checkpoint."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class GenerationWalkthrough:
    def __init__(self, runner):
        self.runner = runner
        self.events = []
        self.started_at = time.monotonic()
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
        if kind == 'Model call finished':
            details.pop('parsed_output', None)
            details.pop('reasoning', None)  # Also present in the response.
            if 'response' in details:
                details['response'] = {key: value for key, value in details['response'].items()
                                       if key in ('state', 'raw_output', 'reasoning', 'metrics')}
        entry = {'sequence': len(self.events) + 1, 'time': datetime.now(UTC).isoformat(),
                 'kind': kind, 'path': path, **copy.deepcopy(details)}
        self.events.append(entry)
        return entry

    def close(self):
        self.closed = True
        self.events.clear()
        self.credentials.clear()

    @contextmanager
    def scope(self, kind, path=None, **details):
        # Never snapshot the sample or copy operation inputs/outputs. The exact
        # model messages and raw response are the authoritative exchange.
        if kind == 'Step':
            definition = details['definition']
            details = {'uses': definition['uses'], 'needs': definition['needs'],
                       'retries': definition['retries'], 'on_failure': definition['on_failure']}
        elif kind == 'Operation':
            details = {'uses': details['uses']}
        started_at = time.monotonic()
        started = self.event(kind + ' started', path, **details)
        result = {}
        try:
            yield result
        except BaseException as error:
            result.update(error={'type': type(error).__name__, 'detail': str(error)})
            raise
        finally:
            if not self.closed:
                if kind == 'Step':
                    record = result.get('record', {})
                    result = {**({'error': result['error']} if 'error' in result else {}),
                              'record': {key: record[key] for key in ('state', 'error', 'detail') if key in record}}
                elif kind == 'Operation':
                    result = {key: value for key, value in result.items() if key == 'error'}
                self.event(kind + ' finished', path, started_event=started['sequence'],
                           elapsed_seconds=round(time.monotonic() - started_at, 3), **result)

    def attempt_started(self):
        runner = self.runner
        self.remember_credentials(runner.config)
        self.event('Generation attempt', scenario=runner.scenario.name,
                   job_id=runner.snapshot['id'], batch=runner.status['batch'],
                   conditions=runner.status['conditions'],
                   settings={key: runner.config.get(key) for key in (
                       'single_generation', 'content_first', 'clock', 'max_tool_calls',
                   )}, max_parallel=runner.snapshot['max_parallel'])

    def finished(self, status):
        # Status also contains cumulative metrics and conditions already shown.
        self.event('Generation finished', status={key: status[key] for key in (
            'phase', 'batch', 'last_error', 'draft_id', 'paused',
        ) if key in status}, elapsed_seconds=round(time.monotonic() - self.started_at, 3))

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
        calls = [e for e in self.events if e['kind'] == 'Model call finished']
        attempts = sum(e['kind'] == 'Generation attempt' for e in self.events)
        model_seconds = sum(e.get('response', {}).get('metrics', {}).get('generation_seconds', 0) for e in calls)
        text.append(f"Attempts: {attempts}. Model calls: {len(calls)}. "
                    f"Reported model time (summed across calls): {model_seconds:.1f}s.\n")
        for entry in self.events:
            entry = redact(entry)
            payload = json.dumps(entry, ensure_ascii=False, indent=2)
            # A generated prompt or response may itself contain Markdown fences.
            fence = '`' * max(3, max((len(m) for m in re.findall(r'`+', payload)), default=0) + 1)
            path = f" ({entry['path']})" if entry.get('path') else ''
            text.append(f"## {entry['sequence']}. {entry['kind']}{path}\n\n{fence}json\n{payload}\n{fence}\n")
        return '\n'.join(text)
