"""Explicit generation recovery retains provenance and never targets another sample."""
import asyncio
import copy
import json
import uuid

import httpx
import pytest

from trajectory_remix.dataset import training_eligible
from trajectory_remix.server import create_app
from test_generation_resume import setup_drafts, commit


async def attention(compiler, tmp_path, problem='edited'):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 1)
    sample = copy.deepcopy(app.state.dataset['samples'][0])
    if problem == 'edited':
        sample['metadata']['manual_edit'] = 'Preserve in archive'
        commit(app, sample, 0)
    else:
        with app.state.dataset.store.connect() as db:
            saved = json.loads(db.execute('SELECT snapshot FROM generation_jobs WHERE id=?', (sample['metadata']['generation_job'],)).fetchone()[0])
            saved['tokenizer_fingerprint'] = 'previous-tokenizer'
            db.execute('UPDATE generation_jobs SET snapshot=? WHERE id=?', (json.dumps(saved), saved['id']))
    runner.resume_queue.start()
    await runner.job
    issue = app.state.generation_recovery.view()['issues'][0]
    return app, runner, fake, issue


def recover(app, issue, action):
    service = app.state.generation_recovery
    return service.start(service.view()['id'], issue['position'], action, issue['source_hash'])


@pytest.mark.parametrize('problem', ['edited','tokenizer'])
async def test_force_resume_overrides_warnings_and_archives_prior_source(compiler, tmp_path, problem):
    app, runner, fake, issue = await attention(compiler, tmp_path, problem)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    ideas = sum(m[0]['content'].startswith('Create a fictional') for m in fake.messages)
    assert issue['actions'] == {'force_resume':True,'restart':True,'delete':True}
    result = recover(app, issue, 'force_resume')
    await runner.job
    assert app.state.dataset['samples'][0]['metadata']['generation_phase'] == 'complete'
    assert app.state.dataset['samples'][0]['uuid'] == before['uuid']
    assert sum(m[0]['content'].startswith('Create a fictional') for m in fake.messages) == ideas
    assert app.state.generation_recovery.archive(result['recovery_id'])['source'] == before
    assert app.state.generation_recovery.archive(result['recovery_id'])['state'] == 'complete'
    assert app.state.generation_recovery.view()['attention_count'] == 0
    assert not training_eligible(app.state.dataset['samples'][0])


async def test_restart_uses_exact_initial_messages_and_same_entry_without_cached_outputs(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    initial_prompt = copy.deepcopy(fake.messages[0])
    old_job = before['metadata']['generation_job']
    fake.messages.clear()
    result = recover(app, issue, 'restart')
    await runner.job
    after = app.state.dataset['samples'][0]
    assert fake.messages[0] == initial_prompt
    assert len(fake.messages) == 2
    assert (after['id'], after['uuid']) == (before['id'], before['uuid'])
    assert after['metadata']['generation_job'] != old_job
    assert len(app.state.dataset['samples']) == 1
    assert after['metadata']['validation_level'] == 0
    archive = app.state.generation_recovery.archive(result['recovery_id'])
    assert archive['source'] == before and archive['checkpoint']['content_requests']
    with app.state.dataset.store.connect() as db:
        assert db.execute('SELECT count(*) FROM generation_jobs').fetchone()[0] == 2


async def test_restart_after_missing_checkpoint_uses_archived_original_context(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    prompt = copy.deepcopy(fake.messages[0])
    job = app.state.dataset['samples'][0]['metadata']['generation_job']
    with app.state.dataset.store.connect() as db:
        db.execute('DELETE FROM generation_jobs WHERE id=?', (job,))
    issue = app.state.generation_recovery.view()['issues'][0]
    assert not issue['actions']['force_resume'] and issue['actions']['restart']
    fake.messages.clear()
    recover(app, issue, 'restart')
    await runner.job
    assert fake.messages[0] == prompt
    assert runner.status['phase'] == 'complete'


async def test_delete_removes_only_target_and_remains_undoable(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    result = recover(app, issue, 'delete')
    assert len(app.state.dataset['samples']) == 0
    assert app.state.generation_recovery.view()['attention_count'] == 0
    assert app.state.generation_recovery.archive(result['recovery_id'])['state'] == 'deleted'
    await app.state.commands.call('undo_edits', {})
    assert app.state.dataset['samples'][0] == before


async def test_source_conflict_and_missing_checkpoint_never_replace_or_delete(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    changed = copy.deepcopy(app.state.dataset['samples'][0])
    changed['metadata']['later_edit'] = 'must not be deleted'
    commit(app, changed, 0)
    with pytest.raises(ValueError, match='changed'):
        recover(app, issue, 'delete')
    assert app.state.dataset['samples'][0] == changed
    with app.state.dataset.store.connect() as db:
        db.execute('DELETE FROM generation_jobs')
    issue = app.state.generation_recovery.view()['issues'][0]
    with pytest.raises(ValueError, match='original checkpoint'):
        recover(app, issue, 'force_resume')
    assert app.state.dataset['samples'][0] == changed


async def test_recovery_cancel_restart_and_process_restart_preserve_work(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    fake.block = True
    result = recover(app, issue, 'restart')
    await asyncio.wait_for(fake.entered.wait(), 5)
    await runner.stop()
    assert not runner.status['running']
    assert app.state.generation_recovery.archive(result['recovery_id'])['state'] == 'interrupted'
    fake.block = False
    reopened = create_app(app.state.dataset.store.path, client=fake)
    issue = reopened.state.generation_recovery.view()['issues'][0]
    assert issue['actions']['force_resume']
    recover(reopened, issue, 'force_resume')
    await reopened.state.scenario_runner.job
    assert reopened.state.dataset['samples'][0]['metadata']['generation_phase'] == 'complete'


async def test_immediate_cancel_releases_recovery_lock(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    result = recover(app, issue, 'restart')
    await runner.stop()
    assert not runner.status['running']
    assert app.state.generation_recovery.archive(result['recovery_id'])['state'] == 'interrupted'


async def test_recovery_api_and_readonly_archive(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver', headers={'X-Remix-Token':app.state.csrf}) as client:
        response = await client.post('/api/scenarios/recover', json={'run_id':app.state.generation_recovery.view()['id'], 'position':issue['position'], 'source_hash':issue['source_hash'], 'action':'restart'})
        assert response.status_code == 200, response.text
        await runner.job
        archive = await client.get('/api/scenarios/recovery/' + response.json()['recovery_id'])
        assert archive.status_code == 200 and archive.json()['checkpoint']
        assert (await client.post('/api/scenarios/recover', json={'action':'invalid'})).status_code == 422


@pytest.mark.parametrize('action', ['force_resume','restart'])
async def test_explicit_recovery_clears_old_approval_and_external_review_accesses_archive(compiler, tmp_path, action):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    source = copy.deepcopy(app.state.dataset['samples'][0])
    source['metadata'].update(validation_level=3, approval={'reviewer':'previous'}, evaluations=[{'valid':True}])
    commit(app, source, 0)
    issue = app.state.generation_recovery.view()['issues'][0]
    result = recover(app, issue, action)
    await runner.job
    after = app.state.dataset['samples'][0]
    assert after['metadata']['validation_level'] == 0
    assert 'approval' not in after['metadata'] and 'evaluations' not in after['metadata']
    record = await app.state.commands.call('get_generation_record', {'sample_id':after['id'], 'recovery_id':result['recovery_id'], 'request_index':0})
    assert record['archived_sample'] == source and record['request']['raw_output']
    assert record['requirements']['context']['scenario']


async def test_force_cannot_bypass_compiler_or_checkpoint_ownership(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    with app.state.dataset.store.connect() as db:
        saved = json.loads(db.execute('SELECT snapshot FROM generation_jobs WHERE id=?', (before['metadata']['generation_job'],)).fetchone()[0])
        saved['checkpoint']['sample']['replay'] = {'0':[{'op':'generate','window':'missing','content':'invalid'}]}
        db.execute('UPDATE generation_jobs SET snapshot=? WHERE id=?', (json.dumps(saved),saved['id']))
    with pytest.raises(ValueError):
        recover(app, issue, 'force_resume')
    assert app.state.dataset['samples'][0] == before
    with app.state.dataset.store.connect() as db:
        saved['checkpoint']['sample']['uuid'] = str(uuid.uuid4())
        db.execute('UPDATE generation_jobs SET snapshot=? WHERE id=?', (json.dumps(saved),saved['id']))
    with pytest.raises(ValueError, match='owned by this trajectory'):
        recover(app, issue, 'force_resume')
    assert app.state.dataset['samples'][0] == before


async def test_old_missing_inputs_only_offers_deletion_and_undo_restores_issue(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    source = copy.deepcopy(app.state.dataset['samples'][0])
    source['metadata']['generation_record']['requirements'].pop('context')
    commit(app, source, 0)
    with app.state.dataset.store.connect() as db:
        db.execute('DELETE FROM generation_jobs')
    issue = app.state.generation_recovery.view()['issues'][0]
    assert issue['actions'] == {'force_resume':False,'restart':False,'delete':True}
    with pytest.raises(ValueError, match='unavailable'):
        recover(app, issue, 'restart')
    recover(app, issue, 'delete')
    await app.state.commands.call('undo_edits', {})
    assert app.state.generation_recovery.view()['attention_count'] == 1


async def test_failed_restart_retains_old_content_and_new_incomplete_work(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    fake.interrupt = True
    result = recover(app, issue, 'restart')
    await runner.job
    after = app.state.dataset['samples'][0]
    assert after['metadata']['generation_phase'] == 'planning'
    assert after['metadata']['generation_job'] != before['metadata']['generation_job']
    assert app.state.generation_recovery.archive(result['recovery_id'])['source'] == before
    next_issue = app.state.generation_recovery.view()['issues'][0]
    assert next_issue['actions']['force_resume'] and next_issue['recovery_state'] == 'failed'
    fake.interrupt = False
    recover(app, next_issue, 'force_resume')
    await runner.job
    assert runner.status['phase'] == 'complete'


async def test_actions_block_while_another_generation_is_running(compiler, tmp_path):
    app, runner, fake, issue = await attention(compiler, tmp_path)
    fake.block = True
    recover(app, issue, 'restart')
    await asyncio.wait_for(fake.entered.wait(), 5)
    with pytest.raises(ValueError, match='Finish or stop'):
        recover(app, issue, 'delete')
    await runner.stop()
