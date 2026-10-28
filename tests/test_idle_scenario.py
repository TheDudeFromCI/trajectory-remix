"""Autonomous idle work: adaptive actions, persistent TODOs, recovery and review."""
import asyncio
import copy
import json

import httpx
import jsonschema
import pytest

from trajectory_remix.compiler import chunks
from trajectory_remix.dataset import training_eligible
from trajectory_remix.server import create_app
from trajectory_remix.training import query_batch, read_plans
from trajectory_remix.trajectory_review import build_checklist
from test_scenario_variations import FourByteTokenizer
from test_scenarios import EXAMPLES, app_for


class IdleClient:
    def __init__(self, kind='project', *, fail_release=False, hold_cycle=None):
        self.kind = kind
        self.fail_release = fail_release
        self.hold_cycle = hold_cycle
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.messages = []

    def workspace(self):
        quiet = self.kind == 'quiet'
        return {
            'identifier': 'idle-project-review',
            'situation': 'Private author description, never agent input.',
            'success_criteria': 'AUTHOR_ONLY_CRITERION: inspect the record, write reviewed, observe success before completing TODO; or stop honestly if blocked/quiet.',
            'idle_context': ('Scheduler: idle tick. No outstanding responsibilities or alarms; no changes needed.' if quiet else
                             'Scheduler: alarm fired for review.' if self.kind == 'alarm' else 'Scheduler: idle tick.') +
                            ('' if quiet else ' Standing policy: review /project/state.txt; write status: reviewed only after inspecting it, then complete review TODO. No external actions authorized.'),
            'files': [] if quiet or self.kind == 'blocked' else [{'path': '/project/state.txt', 'content': 'status: queued'}],
            'operations': [] if quiet else [{'tool': 'note_save', 'arguments': {
                'name': 'review', 'description': 'Inspect /project/state.txt and record review.',
                'status': 'not started', 'priority': 'high'}}],
        }

    async def complete(self, endpoint, messages, tools=None):
        self.messages.append(copy.deepcopy(messages))
        prompt = messages[0]['content']
        assert endpoint['id'] == 'generator', 'No validator endpoint may be used'
        if prompt.startswith('Create a fictional idle workspace'):
            output = self.workspace()
        elif prompt.startswith("Choose Kidra's next idle-work action"):
            data = json.loads(messages[1]['content'])
            cycle = data['cycle']
            if cycle == self.hold_cycle:
                self.entered.set()
                await self.release.wait()
            assert 'AUTHOR_ONLY_CRITERION' not in messages[1]['content']
            assert 'Private author description' not in messages[1]['content']
            visible = data['visible_windows']
            text = json.dumps(visible)
            old = [w['window'] for w in visible if w['type'] in ('reasoning', 'tool')]
            notes = [{'content': 'Inspect the record before acting; retain the TODO and any alarm obligation.'}]
            calls, finished = [], False
            if self.kind == 'quiet':
                assert cycle == 0
                notes = [{'content': 'No pending obligation or alarm is visible. End quietly without inventing work.'}]
                finished = True
            elif cycle == 0:
                assert 'status: queued' not in text  # Setup files are not observations.
                assert any(w['window'] == 'notes' for w in visible)
                calls = [{'tool': 'file_read', 'arguments': {'path': '/project/state.txt'}}]
                if self.kind == 'parallel':
                    notes.append({'content': 'The high-priority review TODO is actionable; no unfinished dependency is listed.'})
                if self.kind == 'alarm':
                    # A real timer observation accompanies the externally supplied
                    # alarm signal; completion still requires the actual follow-up.
                    calls.append({'tool': 'timer', 'arguments': {'mode': 'alarm',
                                  'timestamp': '2030-01-01T09:01:00+00:00', 'timeout_seconds': 120}})
            elif self.kind == 'blocked' and cycle == 1:
                assert 'false' in text
                notes = [{'content': 'The file read failed. Keep review blocked by missing /project/state.txt; do not claim completion.'}]
                calls = [{'tool': 'note_save', 'arguments': {'name': 'review', 'status': 'blocked'}}]
            elif self.kind == 'blocked':
                assert 'blocked' in text
                notes = [{'content': 'The required project file is missing and review remains blocked. No authorized immediate action remains.'}]
                finished = True
            elif cycle == 1:
                assert 'status: queued' in text
                if self.kind == 'alarm':
                    assert 'alarm' in text and 'elapsed_seconds' in text
                notes = [{'content': 'Read succeeded: /project/state.txt contains status: queued. Record reviewed; TODO stays open until the write succeeds.'}]
                calls = [{'tool': 'file_write', 'arguments': {'path': '/project/state.txt', 'content': 'status: reviewed'}}]
                if self.fail_release:
                    old = ['notes']  # Invalid lifecycle proposal must roll back.
            elif cycle == 2:
                assert 'file_write' in text and 'status: reviewed' in text
                notes = [{'content': 'The file_write observation confirms /project/state.txt now contains status: reviewed. Complete the review TODO.'}]
                calls = [{'tool': 'note_save', 'arguments': {'name': 'review', 'status': 'done'}}]
            else:
                assert cycle == 3 and 'done' in text
                notes = [{'content': 'The project write succeeded and review TODO is done. No further current obligation remains; stop quietly.'}]
                finished = True
            output = {'streams': notes, 'calls': calls, 'release_windows': old, 'finished': finished}
        else:
            pytest.fail('Unexpected generation, user, reference, novelty or validator request: ' + prompt[:80])
        return {'content': json.dumps(output)}


def setup_idle(tmp_path, compiler, monkeypatch, client):
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: FourByteTokenizer())
    app = app_for(tmp_path, compiler, client, legacy_reviews=False)
    config = copy.deepcopy(app.state.dataset['config'])
    config['tasks'] = []
    config['endpoints'] = [config['endpoints'][0]]
    config['endpoints'][0].update(inference_tokenizer='fixture', max_tokens=1024)
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    return app, app.state.scenario_runner


@pytest.mark.parametrize('kind,requests', [('project', 5), ('parallel', 5), ('alarm', 5), ('blocked', 4), ('quiet', 2)])
async def test_idle_adaptive_work_without_user_or_local_validation(compiler, tmp_path, monkeypatch, kind, requests):
    client = IdleClient(kind)
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, client)
    runner.start('idle_scenario_noval')
    await asyncio.wait_for(runner.job, 30)
    assert runner.status['phase'] == 'complete', list(runner.logs)
    sample = app.state.dataset['samples'][0]
    assert len(client.messages) == requests
    assert sample['metadata']['validation_level'] == 0 and not training_eligible(sample)
    assert sample['metadata']['tags'][0] == 'idle_scenario'
    assert sample['id'] == 'idle-project-review-1'
    assert set(sample['metadata']['roles']) == {'kidra'}
    assert sample['metadata']['concept']['success_criteria'].startswith('AUTHOR_ONLY_CRITERION')
    assert len(sample['metadata']['review_checklist']['checks']) == 4
    plan = compiler.compile(sample)
    assert not plan['diagnostics']
    assert not any(w['id'] == 'chat' for w in plan['windows'])
    assert not any(e.get('source') == 'user' for es in sample['replay'].values() for e in es)
    assert all(w['close'] is not None for w in plan['windows'] if w['type'] != 'notes')
    assert not any(e.get('window') == 'notes' for es in sample['replay'].values() for e in es)
    visible_calls = [c for c in plan['tool_calls'] if not c.get('invisible')]
    assert not any(c['tool'] == 'chat' for c in visible_calls)
    if kind == 'quiet':
        assert not visible_calls
    elif kind == 'blocked':
        assert not visible_calls[0]['outcome']['ok']
        assert not any(c['arguments'].get('status') == 'done' for c in visible_calls)
    else:
        read = next(c for c in visible_calls if c['tool'] == 'file_read')
        write = next(c for c in visible_calls if c['tool'] == 'file_write')
        done = next(c for c in visible_calls if c['tool'] == 'note_save')
        assert read['end'] < write['request_start'] < done['request_start']
        if kind == 'alarm':
            alarm = next(c for c in visible_calls if c['tool'] == 'timer')
            assert alarm['request_start'] == read['request_start']
            assert alarm['outcome']['ok'] and alarm['end'] < write['request_start']
        if kind == 'parallel':
            analyses = [e for e in plan['events'] if e.get('op') == 'generate'
                        and e.get('window', '').startswith('reasoning-') and e['start'] == read['request_start']]
            assert len(analyses) == 2
        # Results disappear only AFTER new generated working notes are complete.
        read_window = next(w for w in plan['windows'] if w['id'] == read['window'])
        next_notes = [e for e in plan['events'] if e.get('op') == 'generate'
                      and e.get('window', '').startswith('reasoning-') and e['start'] == write['request_start']]
        assert next_notes and read_window['close'] > max(e['end'] for e in next_notes)
    assert all(r['loss_weight'] == 0 for r in plan['rows'] if r['phase'] == 'B')
    for call in visible_calls:
        rows = [r for r in plan['rows'] if r['event'] == call['path'] + '.request']
        assert rows and all(r['loss_weight'] > 0 for r in rows)
    assert all(any(r['token_id'] == compiler.ids[token] for r in plan['rows']) for token in ('bos', 'eos', 'noop'))
    targets = [r for r in plan['rows'] if r['loss_weight'] > 0][::101]
    batch = query_batch(plan, targets)
    memory = [r for r in plan['rows'] if r['durable']]
    for target, mask in zip(targets, batch['attention_allowed']):
        assert all(not yes or (key['tick'] <= target['tick'] and not
                   (key['tick'] == target['tick'] and key['phase'] == 'D')) for key, yes in zip(memory, mask))
    record = sample['metadata']['generation_record']
    assert all(r['metrics']['context_utilization_reserved'] <= 1 for r in record['requests'])
    assert not sample['metadata'].get('evaluations')
    # Export format handles persistent automatic notes without changing approval.
    export = tmp_path / 'unapproved-audit-chunks.jsonl'
    export.write_text('\n'.join(json.dumps(c) for c in chunks(plan, 1000)))
    assert next(iter(read_plans(export)))['rows'] == plan['rows']
    assert not training_eligible(sample)
    reopened = create_app(app.state.dataset.store.path, scenarios_dir=None)
    assert reopened.state.dataset['samples'][0] == sample
    checklist = await reopened.state.commands.call('get_review_checklist', {'sample_id': sample['id']})
    assert checklist['checklist']['scenario'] == 'idle_scenario_noval'
    assert checklist['checklist']['source'] == 'generation_snapshot'


async def test_idle_lifecycle_failure_keeps_content_for_external_repair(compiler, tmp_path, monkeypatch):
    client = IdleClient(fail_release=True)
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, client)
    runner.start('idle_scenario_noval')
    await asyncio.wait_for(runner.job, 30)
    assert runner.status['phase'] == 'rejected', list(runner.logs)
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    record = sample['metadata']['generation_record']
    failure = next(f for f in record['failures'] if f.get('path') == 'work.1.act')
    assert 'persistent notes' in failure['error']
    assert 'Record reviewed' in json.dumps(failure['sample'])
    assert 'Record reviewed' in record['requests'][-1]['raw_output']
    # The failed batch was rolled back to the last valid artifact state.
    plan = compiler.compile(sample)
    assert not any(c['tool'] == 'file_write' and not c.get('invisible') for c in plan['tool_calls'])
    commands = app.state.commands
    prepared = await commands.call('review_trajectory', {'sample_id': sample['id']})
    eid = next((t, i, e) for t, es in sample['replay'].items() for i, e in enumerate(es)
               if e.get('op') == 'generate' and e.get('content', '').startswith('Inspect the record'))
    repaired = await commands.call('repair_trajectory', {
        'sample_id': sample['id'], 'expected_revision': prepared['revision'],
        'checklist_hash': prepared['checklist']['checklist_hash'],
        'edits': [{'action': 'replace_text', 'event_id': f'replay[{eid[0]}][{eid[1]}]',
                   'field': '/content', 'old': 'Inspect the record', 'new': 'Examine the record'}],
    })
    assert repaired['saved'] and repaired['review_required']
    assert repaired['checklist'] == prepared['checklist']
    assert app.state.dataset['samples'][0]['metadata']['concept'] == sample['metadata']['concept']


async def test_idle_cancellation_and_restart_resume_without_regenerating_world(compiler, tmp_path, monkeypatch):
    client = IdleClient(hold_cycle=1)
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, client)
    runner.start('idle_scenario_noval')
    await asyncio.wait_for(client.entered.wait(), 30)
    await runner.stop()
    sample = app.state.dataset['samples'][0]
    assert runner.status['phase'] == 'cancelled' and runner.can_resume(sample['id'])
    resumed_client = IdleClient()
    reopened = create_app(app.state.dataset.store.path, client=resumed_client, scenarios_dir=EXAMPLES)
    resumed = reopened.state.scenario_runner
    resumed.resume_saved(sample['id'])
    await asyncio.wait_for(resumed.job, 30)
    assert resumed.status['phase'] == 'complete', list(resumed.logs)
    assert len(resumed_client.messages) == 3  # Decisions 1, 2, 3 only.
    assert all(m[0]['content'].startswith("Choose Kidra's") for m in resumed_client.messages)
    saved = reopened.state.dataset['samples'][0]
    assert saved['metadata']['validation_level'] == 0
    assert sum(c['tool'] == 'file_read' for c in compiler.compile(saved)['tool_calls']) == 1


def test_idle_only_has_kidra_and_enforces_honest_finished_decisions():
    from trajectory_remix.scenarios import ScenarioRegistry
    scenario = ScenarioRegistry(EXAMPLES).scenarios['idle_scenario_noval']
    assert scenario.parent_name is None and scenario.flow.generation_mode == 'content-first'
    assert set(scenario.flow.roles) == {'kidra'}
    assert len(build_checklist(scenario).checks) == 4
    schema = scenario.flow.schemas['Action']
    action = {'streams': [{'content': 'Finish quietly.'}], 'calls': [], 'release_windows': [], 'finished': True}
    jsonschema.validate(action, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**action, 'calls': [{'tool': 'file_read', 'arguments': {'path': '/x'}}]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**action, 'finished': False, 'calls': [{'tool': 'chat', 'arguments': {'content': 'Hello'}}]}, schema)


@pytest.mark.parametrize('names', [None, 'system', ['unknown'], ['system'], ['system', 'system']])
def test_window_release_rejects_bad_lifecycle_choices_without_mutation(compiler, tmp_path, monkeypatch, names):
    _, runner = setup_idle(tmp_path, compiler, monkeypatch, IdleClient())
    runner.start('idle_scenario_noval', _schedule=False)
    before = copy.deepcopy(runner.builder.sample)
    with pytest.raises(ValueError):
        runner.release_windows(names)
    assert runner.builder.sample == before


async def test_idle_budget_failure_keeps_original_inputs_without_calling_model(compiler, tmp_path, monkeypatch):
    client = IdleClient()
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, client)
    config = copy.deepcopy(app.state.dataset['config'])
    config['content_first'] = {'context_budget': 256, 'reserve_tokens': 32}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('idle_scenario_noval')
    await asyncio.wait_for(runner.job, 15)
    assert not client.messages
    record = app.state.dataset['samples'][0]['metadata']['generation_record']
    assert record['failures'] and 'budget exceeded' in json.dumps(record['failures'])
    assert record['failures'][0]['inputs']['conditions']


async def test_idle_iteration_limit_retains_incomplete_work(compiler, tmp_path, monkeypatch):
    class NeverFinished(IdleClient):
        async def complete(self, endpoint, messages, tools=None):
            if messages[0]['content'].startswith("Choose Kidra's"):
                self.messages.append(copy.deepcopy(messages))
                visible = json.loads(messages[1]['content'])['visible_windows']
                return {'content': json.dumps({'streams': [{'content': 'An unfinished obligation remains; do not claim success.'}],
                    'calls': [], 'release_windows': [w['window'] for w in visible if w['type'] == 'reasoning'], 'finished': False})}
            return await super().complete(endpoint, messages, tools)
    client = NeverFinished()
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, client)
    runner.start('idle_scenario_noval')
    await asyncio.wait_for(runner.job, 30)
    assert runner.status['phase'] == 'rejected'
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    assert len(client.messages) == 13
    assert 'iteration limit' in json.dumps(sample['metadata']['generation_record']['failures'])
    compiler.compile(sample)
    assert 'unfinished obligation' in sample['metadata']['generation_record']['requests'][-1]['raw_output']


async def test_idle_training_export_requires_explicit_ready_approval(compiler, tmp_path, monkeypatch):
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, IdleClient('quiet'))
    runner.start('idle_scenario_noval')
    await runner.job
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                 headers={'X-Remix-Token': app.state.csrf}) as client:
        before = await client.post('/api/export', json={})
        assert before.status_code == 200 and not before.content
        # Explicit approval is exercised only on this disposable test fixture.
        assert (await client.post('/api/samples/0/approve')).status_code == 200
        after = await client.post('/api/export', json={})
        assert after.status_code == 200 and after.content
    exported = tmp_path / 'approved-test-fixture.jsonl'
    exported.write_bytes(after.content)
    plan = compiler.compile(app.state.dataset['samples'][0])
    assert next(iter(read_plans(exported)))['rows'] == plan['rows']


@pytest.mark.parametrize('mode,expected', [('content-first', 1), ('existing', 2)])
def test_adaptive_requests_reuse_visible_contracts_without_changing_existing_mode(compiler, tmp_path, monkeypatch, mode, expected):
    _, runner = setup_idle(tmp_path, compiler, monkeypatch, IdleClient())
    runner.registry.scenarios['idle_scenario_noval'].flow.generation_mode = mode
    if mode == 'existing':
        runner.registry.scenarios['idle_scenario_noval'].flow.steps['validate'].when = False
    runner.start('idle_scenario_noval', _schedule=False)
    for expression in ('2 + 2', '2 + 3'):
        runner.simulate({'calls': [[{'tool': 'calculator', 'arguments': {'expression': expression}}]]})
    shown = [e for es in runner.builder.sample['replay'].values() for e in es if e['op'] == 'show_tools']
    assert len(shown) == expected
    plan = compiler.compile(runner.closed_draft())
    assert not plan['diagnostics']
    assert all(c['outcome']['ok'] for c in plan['tool_calls'])
