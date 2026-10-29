"""The dashboard and MCP share exact, durable custom guidance and review contracts."""
import copy
import json
from pathlib import Path

import pytest

from trajectory_remix.guidance import GUIDE_TOPICS
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.server import create_app
from trajectory_remix.trajectory_review import build_checklist
from test_authoring import call


async def save_guidance(studio, guidance):
    config = copy.deepcopy(studio[0].state.dataset['config'])
    config['review_guidance'] = guidance
    response = await studio[1].put('/api/config', json={'config': config})
    assert response.status_code == 200, response.text


def documents(bundle):
    return {d['id']: d for d in bundle['documents']}


async def test_same_guidance_in_dashboard_and_mcp_preserves_standard_content(studio):
    before = await call(studio, 'get_guidance')
    custom = {'guide': '# Names\nUse randomly generated usernames.',
              'checklist': '- [ ] Reasoning includes a joke.'}
    await save_guidance(studio, custom)
    bundle = await call(studio, 'get_guidance')
    dashboard = await studio[1].get('/api/guidance')
    assert dashboard.status_code == 200
    assert dashboard.json() == bundle
    docs = documents(bundle)
    assert all(docs[f'standard.{topic}'] == documents(before)[f'standard.{topic}'] for topic in GUIDE_TOPICS)
    assert all(not docs[f'standard.{topic}']['editable'] for topic in GUIDE_TOPICS)
    assert docs['custom.dataset.guide']['content'] == custom['guide']
    assert docs['custom.dataset.checklist']['content'] == custom['checklist']
    assert 'endpoints' not in bundle and 'config' not in bundle
    assert (await call(studio, 'get_authoring_guide'))['custom_guidance'][0]['content'] == custom['guide']
    # Persisted in the SQLite project and reloaded by another dashboard/MCP session.
    reopened = create_app(studio[0].state.dataset.store.path)
    assert reopened.state.commands.get_guidance() == bundle
    assert (await call(studio, 'get_session'))['guidance']['tool'] == 'get_guidance'


async def test_custom_requirements_apply_without_scenario_and_bind_review_and_repair(studio):
    await save_guidance(studio, {'guide': 'Use generated names.', 'checklist': '- [ ] Include a joke.'})
    prepared = await call(studio, 'review_trajectory', sample_id='test')
    check = prepared['checklist']
    assert '$custom.dataset' in check['checks']
    assert 'Include a joke' in check['checks']['$custom.dataset']['prompt']
    assert prepared['guidance'] == (await studio[1].get('/api/guidance?sample_id=test')).json()
    arguments = {'sample_id': 'test', 'reviewer': 'Tester', 'summary': 'Checked requirements',
                 'expected_revision': prepared['revision'], 'checklist_hash': check['checklist_hash']}
    missing = await studio[1].post('/api/mcp/call', json={
        'name': 'record_trajectory_review', 'arguments': {**arguments, 'results': {}}})
    assert missing.status_code == 422
    from test_trajectory_review import results_for
    results = results_for(check)
    results['$custom.dataset'] = {'valid': False, 'feedback': ['No joke.']}
    recorded = await call(studio, 'record_trajectory_review', **arguments, results=results)
    assert recorded['validation_level'] == -2
    await save_guidance(studio, {'guide': 'Use different generated names.'})
    revision = studio[0].state.dataset['revision']
    for name, extra in (
        ('record_trajectory_review', {'reviewer': 'Tester', 'summary': 'Old requirements',
                                     'results': {'$custom.dataset': {'valid': True, 'feedback': []}}}),
        ('repair_trajectory', {'edits': [{'action': 'delete', 'event_id': 'replay[0][0]'}]}),
    ):
        response = await studio[1].post('/api/mcp/call', json={'name': name, 'arguments': {
            'sample_id': 'test', 'expected_revision': revision, 'checklist_hash': check['checklist_hash'], **extra}})
        assert response.status_code == 409, response.text
        assert 'Checklist changed' in response.text


async def test_scenario_selection_and_saved_checklist_show_all_prompts(studio):
    registry = ScenarioRegistry(Path(__file__).parent / 'fixtures/retired-scenarios')
    studio[0].state.scenario_runner.registry = registry
    scenario = registry.scenarios['user_problem']
    snapshot = build_checklist(scenario).model_dump()
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {
        'tags': ['user_scenario'], 'scenario': 'user_problem', 'review_checklist': snapshot}})
    await save_guidance(studio, {'guide': 'Global names.', 'scenarios': {
        'user_problem': {'guide': 'Jokes in reasoning.', 'checklist': '- [ ] A pun.'},
        'other': {'guide': 'Not applicable here.'}}})
    bundle = await call(studio, 'get_guidance', sample_id='test')
    assert bundle['scenario'] == 'user_problem'
    docs = documents(bundle)
    assert docs['scenario.checklist']['source'] == 'generation_snapshot'
    assert json.loads(docs['scenario.contract']['content']) == bundle['checklist']
    assert all(docs[f'scenario.prompt.{name}']['content'] == text for name, text in scenario.prompts.items())
    assert docs['custom.scenario.guide']['content'] == 'Jokes in reasoning.'
    assert 'Not applicable here' not in str(bundle['documents'])
    for name, check in snapshot['checks'].items():
        assert docs[f'check.{name}.Review']['content'] == check['prompt']
        assert name in docs['scenario.checklist']['content']
    assert {'$custom.dataset', '$custom.scenario'} <= bundle['checklist']['checks'].keys()
    # Saved checklists keep original prompts after registry changes and removal.
    filename = next(iter(scenario.prompts))
    scenario.prompts[filename] = 'Changed registry guide.'
    selected = await call(studio, 'get_guidance', scenario='user_problem')
    assert documents(selected)[f'scenario.prompt.{filename}']['content'] == 'Changed registry guide.'
    studio[0].state.scenario_runner.registry = ScenarioRegistry()
    saved = await call(studio, 'get_guidance', sample_id='test')
    assert saved['checklist']['checklist_hash'] == bundle['checklist']['checklist_hash']
    assert documents(saved)['scenario.checklist']['content'] == docs['scenario.checklist']['content']
    assert (await call(studio, 'get_trajectory', sample_id='test'))['sample']['metadata']['review_checklist'] == snapshot
    other = await call(studio, 'get_guidance', scenario='other')
    assert documents(other)['custom.scenario.guide']['content'] == 'Not applicable here.'


@pytest.mark.parametrize('guidance', [None, [], {'guide': 1}, {'checklist': 'x' * 200001},
    {'scenarios': []}, {'scenarios': {'': {}}}, {'scenarios': {'x': {'guide': False}}},
    {'standard': 'overwrite'}, {'scenarios': {'x': {'unknown': 'x'}}}])
async def test_invalid_guidance_rejected_without_saving(studio, guidance):
    before = studio[0].state.dataset['revision']
    config = copy.deepcopy(studio[0].state.dataset['config'])
    config['review_guidance'] = guidance
    response = await studio[1].put('/api/config', json={'config': config})
    assert response.status_code == 422, response.text
    assert studio[0].state.dataset['revision'] == before


async def test_scenario_named_dataset_does_not_apply_to_unscoped_samples(studio):
    await save_guidance(studio, {'guide': 'Global.', 'scenarios': {'dataset': {'guide': 'Only for the named scenario.'}}})
    unscoped = await call(studio, 'get_review_checklist', sample_id='test')
    assert set(unscoped['checklist']['checks']) == {'validate.layout', 'validate.tools', 'validate.reasoning', 'validate.correctness', '$custom.dataset'}
    assert len(unscoped['custom_guidance']) == 1
    scoped = await call(studio, 'get_guidance', scenario='dataset')
    assert set(scoped['checklist']['checks']) == {'validate.layout', 'validate.tools', 'validate.reasoning', 'validate.correctness', '$custom.dataset', '$custom.scenario'}
