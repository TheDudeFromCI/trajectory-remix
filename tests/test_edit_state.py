"""Ordinary source edits preserve state across dashboard and authoring saves."""

import pytest

from trajectory_remix.dataset import training_eligible
from trajectory_remix.storage import DatasetStore
from test_authoring import call, source


@pytest.mark.parametrize('level', range(-3, 4))
@pytest.mark.parametrize('automated', [False, True])
@pytest.mark.parametrize('editor', ['dashboard', 'authoring'])
async def test_edit_preserves_state(studio, level, automated, editor):
    app, client = studio
    original = await source(studio)
    original['metadata'] = {
        'validation_level': level,
        'evaluations': [{'report': {'valid': True}}],
    }
    if automated:
        original['metadata']['origin'] = 'automation'
    app.state.dataset['samples'][0] = original

    event = {'op': 'append', 'window': 'a', 'content': 'Corrected typo.'}
    if editor == 'dashboard':
        edited = await source(studio)
        edited['replay']['1'] = [event]
        response = await client.put('/api/samples/0', json={'sample': edited})
        assert response.status_code == 200, response.text
    else:
        await call(studio, 'create_event', sample_id='test', tick='1', event=event)

    saved = await source(studio)
    assert saved['replay']['1'] == [event]
    assert saved['metadata']['validation_level'] == level
    assert 'evaluations' not in saved['metadata']
    assert training_eligible(saved) == (level == 3)
    assert list(DatasetStore(app.state.dataset.store.path).samples)[0] == saved
    state = (await client.get('/api/state')).json()
    assert state['samples'][0]['validation_level'] == level

    if level == 3:
        await call(studio, 'undo_edits')
        assert await source(studio) == original
        await call(studio, 'redo_edits')
        assert await source(studio) == saved
