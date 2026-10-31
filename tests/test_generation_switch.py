"""Paused creation/recovery can release the worker without losing saved work."""
import asyncio
import copy
import json

import httpx
import pytest

from trajectory_remix.server import create_app
from test_idle_scenario import IdleClient
from test_scenarios import app_for
from test_generation_resume import setup_drafts
from test_generation_recovery import attention, recover


async def wait_paused(runner):
    async def ready():
        while not runner.can_suspend():
            if runner.job.done():
                raise AssertionError(('Task ended before pause', runner.status))
            await asyncio.sleep(.005)
    await asyncio.wait_for(ready(), 10)


class PauseAfterWorkspace(IdleClient):
    def __init__(self):
        super().__init__('quiet')
        self.owner = None
        self.pause_next = True

    async def complete(self, endpoint, messages, tools=None):
        response = await super().complete(endpoint, messages, tools)
        if self.pause_next and messages[0]['content'].startswith('Create a fictional idle workspace'):
            self.pause_next = False
            self.owner.pause()
        return response


@pytest.mark.parametrize('new_size', [1, 3])
async def test_switch_to_new_creation_then_restore_original_batch_quota(compiler, tmp_path, new_size):
    fake = PauseAfterWorkspace()
    app = app_for(tmp_path, compiler, fake, scenarios=None, legacy_reviews=False)
    runner = fake.owner = app.state.scenario_runner
    runner.start('idle_scenario', batch_size=2)
    await wait_paused(runner)
    old_id, old_job = runner.status['draft_id'], runner.snapshot['id']
    old_batch = copy.deepcopy(runner.status['batch'])
    original_steps = copy.deepcopy(runner.snapshot['steps'])
    logs = copy.deepcopy(list(runner.logs))
    assert len(fake.messages) == 1
    await runner.suspend()
    assert runner.status['phase'] == 'suspended' and not runner.status['running']
    assert runner.status['batch']['state'] == 'paused'
    assert runner.snapshot['id'] == old_job and runner.snapshot['steps'] == original_steps
    assert list(runner.logs)[:len(logs)] == logs
    assert app.state.dataset.store.listing('@generation:unfinished')[0] == 1
    runner.start('idle_scenario', batch_size=new_size)
    await asyncio.wait_for(runner.job, 15)
    new_batch = runner.status['batch']['id']
    assert new_batch != old_batch['id']
    assert runner.status['batch']['completed'] == new_size
    assert app.state.dataset['samples'][0]['metadata']['generation_phase'] == 'planning'
    with app.state.dataset.store.connect() as db:
        saved = json.loads(db.execute('SELECT snapshot FROM generation_jobs WHERE id=?', (old_job,)).fetchone()[0])
    assert saved['status']['phase'] == 'suspended'
    assert saved['status']['batch']['target'] == 2
    assert saved['steps'] == original_steps
    # A fresh process can return to the original batch, reusing its workspace.
    restored = create_app(app.state.dataset.store.path, client=fake)
    resumed = restored.state.scenario_runner
    resumed.resume_saved(old_id)
    await asyncio.wait_for(resumed.job, 15)
    assert resumed.status['batch']['id'] == old_batch['id']
    assert resumed.status['batch']['target'] == resumed.status['batch']['completed'] == 2
    assert len(restored.state.dataset['samples']) == new_size + 2
    assert sum(m[0]['content'].startswith('Create a fictional idle workspace') for m in fake.messages) == new_size + 2
    assert all(s['metadata']['validation_level'] == 0 for s in restored.state.dataset['samples'])


async def test_pause_and_switch_does_not_cancel_an_active_inference(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 1)
    fake.block = True
    runner.resume_saved(app.state.dataset['samples'][0]['id'])
    await asyncio.wait_for(fake.entered.wait(), 5)
    runner.pause()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                 headers={'X-Remix-Token': app.state.csrf}) as http:
        assert not (await http.get('/api/state')).json()['can_suspend_generation']
        rejected = await http.post('/api/scenarios/suspend')
        assert rejected.status_code == 409
        assert not runner.job.done() and runner.status['active']
        assert 'finish' in rejected.json()['detail']
        fake.release.set()
        await wait_paused(runner)
        assert (await http.get('/api/state')).json()['can_suspend_generation']
        before_calls = fake.calls
        assert (await http.post('/api/scenarios/suspend')).status_code == 200
    assert not runner.status['running'] and runner.status['phase'] == 'suspended'
    assert runner.snapshot['steps']['work.0.decide']['state'] == 'complete'
    fake.block = False
    runner.resume_saved(app.state.dataset['samples'][0]['id'])
    await asyncio.wait_for(runner.job, 10)
    assert runner.status['phase'] == 'complete'
    assert fake.calls == before_calls  # The completed inference is not repeated.


async def test_immediate_creation_pause_can_switch_before_coroutine_starts(compiler, tmp_path):
    fake = IdleClient('quiet')
    app = app_for(tmp_path, compiler, fake, scenarios=None, legacy_reviews=False)
    runner = app.state.scenario_runner
    runner.start('idle_scenario', batch_size=200)
    old_job = runner.snapshot['id']
    runner.pause()
    await runner.suspend()
    assert not fake.messages
    assert runner.snapshot['id'] == old_job
    assert runner.status['batch']['target'] == 200 and runner.status['batch']['state'] == 'paused'
    runner.start('idle_scenario')
    await asyncio.wait_for(runner.job, 10)
    assert runner.status['batch']['target'] == runner.status['batch']['completed'] == 1
    assert app.state.dataset['samples'][0]['metadata']['generation_phase'] == 'planning'


@pytest.mark.parametrize('immediate', [True, False])
async def test_switch_from_resume_pass_and_later_finish_only_remaining_drafts(compiler, tmp_path, immediate):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 3)
    if not immediate:
        original = runner.run_attempt
        paused = False

        async def pause_after_first():
            nonlocal paused
            await original()
            if not paused:
                paused = True
                runner.pause()
        runner.run_attempt = pause_after_first
    old_run = runner.resume_queue.start()
    if immediate:
        runner.pause()
    else:
        await wait_paused(runner)
    await runner.suspend()
    processed = 0 if immediate else 1
    assert runner.resume_queue.status['state'] == 'suspended'
    assert runner.resume_queue.status['processed'] == processed
    assert not runner.resume_queue.status['running']
    runner.start('idle_scenario', batch_size=2)
    await asyncio.wait_for(runner.job, 15)
    assert runner.status['batch']['completed'] == 2
    assert runner.resume_queue.status['id'] == old_run
    assert runner.resume_queue.status['state'] == 'suspended'
    assert len(app.state.dataset['samples']) == 5
    restored = create_app(app.state.dataset.store.path, client=fake)
    resumed = restored.state.scenario_runner
    assert resumed.resume_queue.status['state'] == 'suspended'
    assert resumed.resume_queue.count() == 3 - processed
    resumed.resume_queue.start()
    await asyncio.wait_for(resumed.job, 15)
    assert resumed.resume_queue.status['target'] == resumed.resume_queue.status['completed'] == 3 - processed
    assert len(restored.state.dataset['samples']) == 5
    assert all(s['metadata']['generation_phase'] == 'complete' for s in restored.state.dataset['samples'])


async def test_suspend_resume_pass_in_current_draft_keeps_pending_item_and_cached_content(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    original = fake.complete
    paused = False

    async def pause_after_decision(*args, **kwargs):
        nonlocal paused
        result = await original(*args, **kwargs)
        if not paused and args[1][0]['content'].startswith("Choose Kidra's next"):
            paused = True
            runner.pause()
        return result
    fake.complete = pause_after_decision
    runner.resume_queue.start()
    await wait_paused(runner)
    saved_id = runner.status['draft_id']
    await runner.suspend()
    assert runner.resume_queue.status['processed'] == 0
    assert runner.resume_queue.results()['items'][0]['state'] == 'suspended'
    assert runner.snapshot['steps']['work.0.decide']['state'] == 'complete'
    previous_calls = fake.calls
    runner.start('idle_scenario')
    await asyncio.wait_for(runner.job, 10)
    runner.resume_queue.start()
    await asyncio.wait_for(runner.job, 10)
    assert runner.resume_queue.status['completed'] == 2
    assert fake.calls == previous_calls + 3  # Two new sample calls; one remaining old draft call.
    assert app.state.dataset['samples'][0]['id'] == saved_id


@pytest.mark.parametrize('action', ['restart', 'force_resume'])
async def test_recovery_can_be_saved_before_starting_another_task(compiler, tmp_path, action):
    app, runner, _, issue = await attention(compiler, tmp_path)
    result = recover(app, issue, action)
    original_id = runner.status['draft_id']
    original_job = runner.snapshot['id']
    runner.pause()
    await runner.suspend()
    assert app.state.generation_recovery.archive(result['recovery_id'])['state'] == 'suspended'
    assert not runner.status['running'] and runner.active_recovery_id is None
    runner.start('idle_scenario')
    await asyncio.wait_for(runner.job, 10)
    assert len(app.state.dataset['samples']) == 2
    runner.resume_saved(original_id)
    await asyncio.wait_for(runner.job, 10)
    assert runner.status['phase'] == 'complete'
    assert runner.snapshot['id'] == original_job
    assert app.state.dataset['samples'][0]['metadata']['validation_level'] == 0


async def test_suspend_endpoint_is_safe_when_no_task_is_running(studio):
    app, http = studio
    before = copy.deepcopy(app.state.scenario_runner.status)
    assert (await http.post('/api/scenarios/suspend')).status_code == 200
    assert app.state.scenario_runner.status == before


async def test_paused_creation_can_switch_to_resume_existing_without_filling_batch_quota(compiler, tmp_path):
    fake = PauseAfterWorkspace()
    app = app_for(tmp_path, compiler, fake, scenarios=None, legacy_reviews=False)
    runner = fake.owner = app.state.scenario_runner
    runner.start('idle_scenario', batch_size=200)
    await wait_paused(runner)
    original_id = runner.status['draft_id']
    await runner.suspend()
    runner.resume_queue.start()
    await asyncio.wait_for(runner.job, 10)
    assert runner.resume_queue.status['target'] == runner.resume_queue.status['completed'] == 1
    assert len(app.state.dataset['samples']) == 1
    assert app.state.dataset['samples'][0]['id'] == original_id
    assert app.state.dataset['samples'][0]['metadata']['generation_phase'] == 'complete'
