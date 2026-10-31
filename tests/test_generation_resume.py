"""Dataset-wide recovery resumes existing automation once and never creates replacements."""
import asyncio
import copy
import json
import uuid

import httpx
import pytest

from trajectory_remix.server import create_app
from test_idle_scenario import IdleClient
from test_scenarios import app_for


class InterruptedIdle(IdleClient):
    def __init__(self):
        super().__init__('quiet')
        self.interrupt = True
        self.block = False
        self.calls = 0

    async def complete(self, endpoint, messages, tools=None):
        self.calls += 1
        if messages[0]['content'].startswith("Choose Kidra's next"):
            if self.block:
                self.entered.set()
                await self.release.wait()
            if self.interrupt:
                raise httpx.ConnectError('simulated interrupted generation')
        return await super().complete(endpoint, messages, tools)


async def setup_drafts(compiler, tmp_path, count=3):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fake = InterruptedIdle()
    app = app_for(tmp_path, compiler, fake, scenarios=None, legacy_reviews=False)
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner = app.state.scenario_runner
    for _ in range(count):
        runner.start('idle_scenario')
        await runner.job
        assert runner.status['phase'] == 'api_paused'
    fake.interrupt = False
    return app, runner, fake


def commit(app, sample, index=None):
    samples = app.state.dataset['samples'].copy()
    if index is None:
        samples.append(sample)
    else:
        samples[index] = sample
    app.state.runner.history.commit(samples, 'Test edit')


async def test_resume_all_finishes_only_existing_automated_drafts_and_reuses_work(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path)
    # A manual draft, even with a planning flag, has no generated job provenance.
    manual = {'id': 'manual', 'uuid': str(uuid.uuid4()), 'replay': {},
              'metadata': {'generation_phase': 'planning', 'origin': 'manual'}}
    commit(app, manual)
    # A manually duplicated generated trajectory retains metadata, but its UUID
    # cannot own the original generation checkpoint.
    duplicate = copy.deepcopy(app.state.dataset['samples'][0])
    duplicate.update(id='manual-copy', uuid=str(uuid.uuid4()))
    commit(app, duplicate)
    ids = [s['id'] for s in app.state.dataset['samples']]
    ideas = sum(m[0]['content'].startswith('Create a fictional') for m in fake.messages)
    assert runner.resume_queue.count() == 3
    assert app.state.dataset.store.listing('@generation:unfinished')[0] == runner.resume_queue.count()
    runner.resume_queue.start()
    await runner.job
    status = runner.resume_queue.status
    assert (status['target'], status['processed'], status['completed'], status['failed'], status['skipped']) == (3, 3, 3, 0, 0)
    assert not status['running'] and runner.status['phase'] == 'resume_complete'
    assert runner.status['activity'] == 'resume-all'
    reopened = create_app(app.state.dataset.store.path, client=fake)
    assert reopened.state.scenario_runner.status['activity'] == 'resume-all'
    assert [s['id'] for s in app.state.dataset['samples']] == ids
    assert all(app.state.dataset['samples'][i]['metadata']['generation_phase'] == 'complete' for i in range(3))
    assert sum(m[0]['content'].startswith('Create a fictional') for m in fake.messages) == ideas
    assert app.state.dataset['samples'][3] == manual
    assert app.state.dataset['samples'][4] == duplicate
    assert runner.resume_queue.count() == 0
    assert app.state.dataset.store.listing('@generation:unfinished')[0] == 0
    assert app.state.dataset.store.listing('@generation:complete')[0] == 3
    assert all(s['metadata']['validation_level'] == 0 for s in list(app.state.dataset['samples'])[:3])


@pytest.mark.parametrize('problem', ['edited', 'missing-checkpoint', 'corrupt-checkpoint', 'provider', 'construction'])
async def test_resume_pass_reports_unusable_entry_and_continues(compiler, tmp_path, problem):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    first = copy.deepcopy(app.state.dataset['samples'][0])
    if problem == 'edited':
        first['metadata']['manual_edit'] = 'Keep this edit'
        commit(app, first, 0)
    elif problem in ('missing-checkpoint', 'corrupt-checkpoint'):
        with app.state.dataset.store.connect() as db:
            if problem == 'missing-checkpoint':
                db.execute('DELETE FROM generation_jobs WHERE id=?', (first['metadata']['generation_job'],))
            else:
                db.execute('UPDATE generation_jobs SET snapshot=? WHERE id=?', ('{}', first['metadata']['generation_job']))
    elif problem == 'provider':
        original = fake.complete
        first_call = True
        async def fail_once(*args, **kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                raise httpx.ConnectError('offline for one draft')
            return await original(*args, **kwargs)
        fake.complete = fail_once
    else:
        original = fake.complete
        first_call = True
        async def invalid_once(*args, **kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                return {'content': json.dumps({'streams': [{'content': 'invalid <|eos|> prose'}],
                    'calls': [], 'release_windows': [], 'finished': True})}
            return await original(*args, **kwargs)
        fake.complete = invalid_once
    runner.resume_queue.start()
    await runner.job
    assert len(app.state.dataset['samples']) == 2
    assert runner.resume_queue.status['processed'] == 2
    assert runner.resume_queue.status['completed'] == 1
    assert runner.resume_queue.status['failed'] + runner.resume_queue.status['skipped'] == 1
    assert app.state.dataset['samples'][1]['metadata']['generation_phase'] == 'complete'
    if problem in ('edited', 'missing-checkpoint', 'corrupt-checkpoint'):
        assert app.state.dataset['samples'][0] == first
    assert runner.resume_queue.results()['items'][0]['error']


async def test_bulk_resume_never_creates_unfilled_original_batch_quota(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 1)
    sample = app.state.dataset['samples'][0]
    with app.state.dataset.store.connect() as db:
        job = sample['metadata']['generation_job']
        saved = json.loads(db.execute('SELECT snapshot FROM generation_jobs WHERE id=?', (job,)).fetchone()[0])
        saved['status']['batch'].update(target=30, attempt_limit=90)
        db.execute('UPDATE generation_jobs SET snapshot=? WHERE id=?', (json.dumps(saved), job))
    runner.resume_queue.start()
    await runner.job
    assert len(app.state.dataset['samples']) == 1
    assert runner.resume_queue.status['completed'] == 1


async def test_cancel_restart_and_rerun_finish_remaining_drafts(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    fake.block = True
    runner.resume_queue.start()
    await asyncio.wait_for(fake.entered.wait(), 5)
    # New jobs, reviews and migrations cannot overlap the resume pass.
    with pytest.raises(ValueError, match='already running'):
        runner.resume_queue.start()
    with pytest.raises(ValueError, match='already running'):
        runner.start('idle_scenario')
    with pytest.raises(ValueError, match='stop generation'):
        app.state.validation_runner.start(app.state.dataset['samples'][0]['id'])
    await runner.stop()
    assert runner.resume_queue.status['state'] == 'cancelled'
    assert not runner.status['running']
    fake.block = False
    reopened = create_app(app.state.dataset.store.path, client=fake)
    queue = reopened.state.scenario_runner.resume_queue
    queue.start()
    await reopened.state.scenario_runner.job
    assert queue.status['completed'] == 2 and queue.count() == 0
    assert len(reopened.state.dataset['samples']) == 2


async def test_pause_resume_and_immediate_cancellation(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    runner.resume_queue.start()
    runner.pause()
    await asyncio.sleep(0)
    assert runner.status['running'] and runner.status['paused']
    assert runner.resume_queue.status['processed'] == 0
    runner.resume()
    await runner.job
    assert runner.resume_queue.status['completed'] == 2
    # Stop before the scheduled coroutine has had a chance to enter its try block.
    app2, other, _ = await setup_drafts(compiler, tmp_path / 'another', 1)
    other.resume_queue.start()
    await other.stop()
    assert not other.status['running'] and not other.resume_queue.status['running']


async def test_resume_all_api_ignores_current_filter_and_selection(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver', headers={'X-Remix-Token': app.state.csrf}) as client:
        view = (await client.get('/api/state', params={'query': '#no-matching-samples', 'limit': 1})).json()
        assert not view['samples'] and view['unfinished_generation_count'] == 2
        started = await client.post('/api/scenarios/resume-all')
        assert started.status_code == 202, started.text
        await runner.job
        result = (await client.get('/api/scenarios/resume-all/results', params={'offset': 1, 'limit': 1})).json()
        assert result['total'] == 2 and len(result['items']) == 1 and result['items'][0]['state'] == 'complete'
        assert (await client.post('/api/scenarios/resume-all')).status_code == 422


@pytest.mark.parametrize('change', ['deleted', 'completed', 'changed-job'])
async def test_queue_rechecks_entries_changed_before_their_turn(compiler, tmp_path, change):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    fake.block = True
    runner.resume_queue.start()
    await asyncio.wait_for(fake.entered.wait(), 5)
    samples = app.state.dataset['samples'].copy()
    if change == 'deleted':
        del samples[1]
    else:
        changed = copy.deepcopy(samples[1])
        changed['metadata']['generation_phase' if change == 'completed' else 'generation_job'] = 'complete' if change == 'completed' else 'another-job'
        samples[1] = changed
    app.state.runner.history.commit(samples, 'Concurrent edit before queue turn')
    fake.block = False
    fake.release.set()
    await runner.job
    assert runner.resume_queue.status['completed'] == 1
    assert runner.resume_queue.status['skipped'] == 1
    if change != 'deleted':
        assert app.state.dataset['samples'][1] == changed


async def test_interrupted_queue_status_survives_process_restart(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 1)
    runner.resume_queue.start()
    await runner.stop()
    # Emulate the last committed status before an unclean process exit.
    runner.resume_queue.status.update(running=True, state='running')
    runner.resume_queue.persist()
    restored = create_app(app.state.dataset.store.path, client=fake)
    queue = restored.state.scenario_runner.resume_queue
    assert queue.status['state'] == 'interrupted' and not queue.status['running']
    queue.start()
    await restored.state.scenario_runner.job
    assert queue.status['completed'] == 1
