"""Activity history must never be rewound by sample checkpoint restoration."""
import copy
import json

import httpx
import pytest

from trajectory_remix.generation_log import GenerationLog
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore
from test_generation_resume import setup_drafts
from test_generation_recovery import attention, recover
from test_scenarios import app_for, ScenarioClient


def event(n, message=None):
    return {'time': f'2026-10-29T19:00:{n:02d}+00:00', 'level': 'info', 'step': None,
            'message': message or f'event {n}'}


def legacy_store(tmp_path, compiler):
    store = DatasetStore.initialize(tmp_path / 'legacy-logs.sqlite', compiler)
    a, b, c, d = [event(i) for i in range(4)]
    # A later-restored older checkpoint has less history than another saved job.
    histories = [[a, b, b], [a, b, b, c, d], [a, b, b, c]]
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY,snapshot TEXT NOT NULL)')
        for i, entries in enumerate(histories):
            db.execute('INSERT INTO generation_jobs VALUES (?,?)', (str(i), json.dumps({'logs': entries})))
        db.execute("INSERT INTO generation_jobs VALUES ('broken','not JSON')")
    return store, [a, b, b, c, d]


def test_legacy_import_unions_overlapping_checkpoints_without_losing_repeated_events(tmp_path, compiler):
    store, expected = legacy_store(tmp_path, compiler)
    journal = GenerationLog(store)
    assert list(journal.entries()) == expected
    assert list(GenerationLog(store).entries()) == expected  # Import is idempotent.
    repeated = event(5, 'An identical event can legitimately occur twice.')
    journal.append(repeated)
    journal.append(repeated)
    assert list(journal.entries()) == [*expected, repeated, repeated]


def test_clear_never_reimports_archived_checkpoint_logs(tmp_path, compiler):
    store, _ = legacy_store(tmp_path, compiler)
    journal = GenerationLog(store)
    journal.clear()
    restored = GenerationLog(store)
    assert list(restored.entries()) == []
    restored.append(event(9))
    assert list(GenerationLog(store).entries()) == [event(9)]
    with store.connect() as db:
        assert json.loads(db.execute("SELECT snapshot FROM generation_jobs WHERE id='1'").fetchone()[0])['logs']


def test_journal_has_no_automatic_entry_limit(tmp_path, compiler):
    store, expected = legacy_store(tmp_path, compiler)
    journal = GenerationLog(store)
    # Exceed the old runner's 250-entry deque limit and typical display limits.
    additions = [{'time': '2026-10-30T00:00:00Z', 'message': f'Long batch entry {i}'} for i in range(1001)]
    for entry in additions:
        journal.append(entry)
    assert list(GenerationLog(store).entries()) == [*expected, *additions]
    journal.clear()
    assert not list(journal.entries())


async def test_resume_older_failed_sample_pause_and_process_restart_keep_full_activity(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 3)
    older = app.state.dataset['samples'][0]['id']
    runner.log('Latest activity after all three samples', 'warning')
    previous = copy.deepcopy(list(runner.logs))
    runner.resume_saved(older, _schedule=False)
    runner.pause()
    assert list(runner.logs)[:len(previous)] == previous
    assert runner.logs[-1]['message'].startswith('Pause requested')
    restored = create_app(app.state.dataset.store.path, client=fake)
    assert list(restored.state.scenario_runner.logs) == list(runner.logs)
    assert restored.state.scenario_runner.status['phase'] == 'interrupted'
    assert restored.state.scenario_runner.snapshot['logs'] == []
    assert restored.state.scenario_runner.snapshot['log_storage'] == 'journal'


async def test_resume_pass_appends_every_samples_activity_without_replacing_prior_entries(compiler, tmp_path):
    app, runner, _ = await setup_drafts(compiler, tmp_path, 3)
    previous = copy.deepcopy(list(runner.logs))
    jobs = {s['metadata']['generation_job'] for s in app.state.dataset['samples']}
    runner.resume_queue.start()
    await runner.job
    entries = list(runner.logs)
    assert entries[:len(previous)] == previous
    assert sum(e['message'] == 'Scenario complete' for e in entries[len(previous):]) == 3
    assert {e['job_id'] for e in entries[len(previous):]} == jobs
    assert list(runner.log_journal.entries()) == entries
    restored = create_app(app.state.dataset.store.path)
    assert list(restored.state.scenario_runner.logs) == entries


@pytest.mark.parametrize('action', ['restart', 'force_resume'])
async def test_recovery_keeps_prior_failed_activity_and_distinguishes_jobs(compiler, tmp_path, action):
    app, runner, _, issue = await attention(compiler, tmp_path)
    previous = copy.deepcopy(list(runner.logs))
    old_job = app.state.dataset['samples'][0]['metadata']['generation_job']
    recover(app, issue, action)
    await runner.job
    assert list(runner.logs)[:len(previous)] == previous
    assert runner.status['phase'] == 'complete'
    new_job = app.state.dataset['samples'][0]['metadata']['generation_job']
    assert (new_job == old_job) is (action == 'force_resume')
    assert all(e['job_id'] == new_job for e in list(runner.logs)[len(previous):])
    assert list(runner.log_journal.entries()) == list(runner.logs)


async def test_log_write_survives_failure_before_checkpoint_persistence(compiler, tmp_path, monkeypatch):
    app = app_for(tmp_path, compiler, ScenarioClient())
    runner = app.state.scenario_runner

    def failed_persist():
        raise RuntimeError('Simulated crash before checkpoint write')

    monkeypatch.setattr(runner, 'persist', failed_persist)
    with pytest.raises(RuntimeError, match='Simulated crash'):
        runner.log('Important diagnostic without a generation checkpoint', 'error')
    restored = create_app(app.state.dataset.store.path)
    assert restored.state.scenario_runner.snapshot is None
    assert list(restored.state.scenario_runner.logs) == list(runner.logs)


async def test_log_export_reads_complete_journal_and_clear_survives_old_checkpoint_resume(compiler, tmp_path):
    app, runner, fake = await setup_drafts(compiler, tmp_path, 3)
    previous = copy.deepcopy(list(runner.logs))
    older = app.state.dataset['samples'][0]['id']
    # Simulate a historical checkpoint still carrying its old cumulative log.
    with app.state.dataset.store.connect() as db:
        rows = db.execute('SELECT id,snapshot FROM generation_jobs').fetchall()
        for job_id, raw in rows:
            saved = json.loads(raw)
            saved['logs'] = previous[:2]
            db.execute('UPDATE generation_jobs SET snapshot=? WHERE id=?', (json.dumps(saved), job_id))
    runner.resume_saved(older, _schedule=False)
    runner.pause()
    runner.log('A log entry newer than the browser last polled')
    through = runner.log_journal.last_sequence()
    prefix_export = runner.log_journal.text(through)
    runner.log('Subsequent events should not extend an export already captured')
    assert 'Subsequent events' not in ''.join(prefix_export)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                 headers={'X-Remix-Token': app.state.csrf}) as http:
        exported = await http.get('/api/scenarios/log')
        assert exported.status_code == 200
        assert exported.headers['content-disposition'] == 'attachment; filename="generation.log"'
        assert all(e['message'] in exported.text for e in runner.logs)
        assert 'A log entry newer than the browser' in exported.text
        assert '[sample=' in exported.text and '[job=' in exported.text and '[batch=' in exported.text
        state = (await http.get('/api/state')).json()
        assert state['logs'] == list(runner.logs)
        snapshot = (await http.get('/api/scenarios/job')).json()
        assert snapshot['logs'] == list(runner.logs)
        assert (await http.post('/api/scenarios/log/clear')).status_code == 200
    assert not runner.logs
    restored = create_app(app.state.dataset.store.path, client=fake)
    resumed = restored.state.scenario_runner
    assert not resumed.logs
    resumed.resume_saved(older, _schedule=False)
    assert len(resumed.logs) == 1
    assert resumed.logs[-1]['message'].startswith('Resuming saved generation checkpoint')
    assert resumed.snapshot['content_requests']  # Clearing logs does not delete model evidence.


async def test_deleting_sample_does_not_delete_its_activity(compiler, tmp_path):
    app, runner, _, issue = await attention(compiler, tmp_path)
    previous = copy.deepcopy(list(runner.logs))
    recover(app, issue, 'delete')
    assert not len(app.state.dataset['samples'])
    assert list(runner.logs) == previous
    assert list(runner.log_journal.entries()) == previous
    restored = create_app(app.state.dataset.store.path)
    assert list(restored.state.scenario_runner.logs) == previous


async def test_cancel_preserves_prior_and_active_sample_entries(compiler, tmp_path):
    import asyncio

    app, runner, fake = await setup_drafts(compiler, tmp_path, 2)
    fake.block = True
    runner.resume_saved(app.state.dataset['samples'][0]['id'])
    await asyncio.wait_for(fake.entered.wait(), 5)
    previous = copy.deepcopy(list(runner.logs))
    await runner.stop()
    assert list(runner.logs)[:len(previous)] == previous
    assert any(e['message'].startswith('Stopped; checkpoint') for e in list(runner.logs)[len(previous):])
    restored = create_app(app.state.dataset.store.path)
    assert list(restored.state.scenario_runner.logs) == list(runner.logs)
