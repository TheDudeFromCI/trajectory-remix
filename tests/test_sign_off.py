"""External reviews are explicit, revision-bound, undoable, and API-free."""

import hashlib
import json
from datetime import datetime

import pytest

from trajectory_remix.dataset import evaluation_source, training_eligible
from trajectory_remix.storage import DatasetStore
from test_authoring import call, source


async def sign_off(studio, **overrides):
    return await studio[1].post('/api/mcp/call', json={
        'name': 'sign_off_trajectory',
        'arguments': {
            'sample_id': 'test', 'verdict': 'validated',
            'reviewer': 'GPT-6.1 via Codex',
            'summary': 'Reviewed content, causality, tool use, and chat delivery.',
            'expected_revision': studio[0].state.dataset['revision'],
            **overrides,
        },
    })


async def test_sign_off_persists_review_without_model_calls_and_is_undoable(studio):
    app, _ = studio

    class NoAPI:
        async def complete(self, *args, **kwargs):
            pytest.fail('External sign-off must not call a model endpoint')

    app.state.commands.client = NoAPI()
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={
        'metadata': {
            'origin': 'automation', 'validation_level': 0,
            'evaluations': [{'level': 1, 'report': {'valid': True}}],
            'approval': {'reviewer': 'Old approval'},
        },
    })
    before = await source(studio)
    revision = app.state.dataset['revision']
    response = await sign_off(studio)
    assert response.status_code == 200, response.text
    result = response.json()
    sample = await source(studio)
    assert sample['metadata']['validation_level'] == result['validation_level'] == 2
    assert result['revision'] == revision + 1
    assert result['diagnostics']  # Unclosed windows are warnings, not compiler errors.
    review = sample['metadata']['evaluations'][-1]
    assert sample['metadata']['evaluations'][0] == before['metadata']['evaluations'][0]
    assert 'approval' not in sample['metadata']
    assert review == result['evaluation']
    assert review['origin'] == 'mcp_sign_off'
    assert review['reviewer'] == 'GPT-6.1 via Codex'
    assert review['reviewed_revision'] == revision
    assert review['report']['valid'] is True
    assert datetime.fromisoformat(review['time']).tzinfo
    assert review['source_hash'] == hashlib.sha256(
        json.dumps(evaluation_source(sample), sort_keys=True).encode()
    ).hexdigest()
    assert not training_eligible(sample)
    assert list(DatasetStore(app.state.dataset.store.path).samples)[0] == sample
    assert (await call(studio, 'list_trajectories', query='@state:validated'))['total'] == 1
    await call(studio, 'undo_edits')
    assert await source(studio) == before
    await call(studio, 'redo_edits')
    assert await source(studio) == sample


@pytest.mark.parametrize('automated,expected', [(True, 0), (False, -3)])
async def test_edit_after_sign_off_invalidates_review(studio, automated, expected):
    if automated:
        await call(studio, 'update_trajectory_metadata', sample_id='test', patch={
            'metadata': {'origin': 'automation'},
        })
    assert (await sign_off(studio)).status_code == 200
    await call(studio, 'create_event', sample_id='test', tick='1', event={
        'op': 'append', 'window': 'a', 'content': 'New evidence requires another review.',
    })
    sample = await source(studio)
    assert sample['metadata']['validation_level'] == expected
    assert 'evaluations' not in sample['metadata']
    assert (await call(studio, 'list_trajectories', query='@state:validated'))['total'] == 0


async def test_compile_failure_cannot_be_signed_off_but_can_be_rejected(studio):
    await call(studio, 'create_event', sample_id='test', tick='1', event={
        'op': 'generate', 'window': 'a', 'content': 'broken{N_TICKS:1}',
    })
    before, revision = await source(studio), studio[0].state.dataset['revision']
    response = await sign_off(studio)
    assert response.status_code == 422
    assert await source(studio) == before
    assert studio[0].state.dataset['revision'] == revision
    response = await sign_off(studio, verdict='rejected', summary='Invalid tick marker.')
    assert response.status_code == 200, response.text
    assert response.json()['validation_level'] == -2
    assert response.json()['evaluation']['report']['valid'] is False
    assert response.json()['diagnostics'][0]['severity'] == 'error'
    assert (await call(studio, 'list_trajectories', query='@state:rejected'))['total'] == 1


@pytest.mark.parametrize('overrides', [
    {'expected_revision': 99}, {'expected_revision': True},
    {'verdict': 'ready'}, {'reviewer': '  '}, {'summary': '\n'},
])
async def test_invalid_or_stale_sign_off_does_not_modify_sample(studio, overrides):
    before, revision = await source(studio), studio[0].state.dataset['revision']
    response = await sign_off(studio, **overrides)
    assert response.status_code in (409, 422), response.text
    assert await source(studio) == before
    assert studio[0].state.dataset['revision'] == revision


async def test_sign_off_requires_reviewed_revision(studio):
    response = await studio[1].post('/api/mcp/call', json={
        'name': 'sign_off_trajectory', 'arguments': {
            'sample_id': 'test', 'verdict': 'validated', 'reviewer': 'Codex',
            'summary': 'Reviewed.',
        },
    })
    assert response.status_code == 422


async def test_only_current_generation_is_locked_for_sign_off(studio):
    runner = studio[0].state.scenario_runner
    runner.status.update(running=True, draft_id='test')
    try:
        response = await sign_off(studio)
        assert response.status_code == 409 and response.json()['code'] == 'busy'
        runner.status['draft_id'] = 'another-trajectory'
        assert (await sign_off(studio)).status_code == 200
    finally:
        runner.status['running'] = False


async def test_concurrent_writer_cannot_sign_off_obsolete_content(studio, monkeypatch):
    app, _ = studio
    compile_sample = app.state.commands.compiler.compile
    other_store = DatasetStore(app.state.dataset.store.path)

    def compile_then_external_edit(sample):
        plan = compile_sample(sample)
        updated = other_store.samples[0]
        updated['replay']['1'] = [{'op': 'append', 'window': 'a', 'content': 'Concurrent edit.'}]
        other_store.samples[0] = updated
        return plan

    monkeypatch.setattr(app.state.commands.compiler, 'compile', compile_then_external_edit)
    response = await sign_off(studio)
    assert response.status_code == 409, response.text
    sample = await source(studio)
    assert sample['replay']['1'][0]['content'] == 'Concurrent edit.'
    assert 'evaluations' not in sample.get('metadata', {})
