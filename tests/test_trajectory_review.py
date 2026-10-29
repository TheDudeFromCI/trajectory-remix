"""Scenario-aware, API-free MCP reviews and atomic targeted repairs."""

import asyncio
import json
import shutil

import jsonschema
import pytest

from trajectory_remix.server import create_app
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.trajectory_review import build_checklist, public_checklist
from test_authoring import call, source
from test_scenarios import EXAMPLES, ScenarioClient, app_for, finish, write_scenario


async def configure(studio):
    app, _ = studio
    app.state.scenario_runner.registry = ScenarioRegistry(EXAMPLES)
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={
        'metadata': {'tags': ['user_scenario', 'coding'], 'origin': 'automation'}
    })
    return await call(studio, 'review_trajectory', sample_id='test')


def results_for(checklist):
    return {name: {'checks': {
        rid: {'evidence': 'replay[0][0]: inspected source and relevant dependencies',
              'status': 'pass', 'feedback': ''}
        for rid in check['rules']
    }} for name, check in checklist['checks'].items()}


async def submit(studio, prepared, results=None, **overrides):
    return await studio[1].post('/api/mcp/call', json={
        'name': 'record_trajectory_review', 'arguments': {
            'sample_id': 'test', 'results': results or results_for(prepared['checklist']),
            'reviewer': 'MCP reviewer', 'summary': 'Reviewed every semantic requirement.',
            'expected_revision': prepared['revision'],
            'checklist_hash': prepared['checklist']['checklist_hash'], **overrides,
        }
    })


async def test_tag_resolution_automatically_exposes_exact_checklist_without_api_calls(studio):
    class NoAPI:
        async def complete(self, *args, **kwargs):
            pytest.fail('MCP review must not call model endpoints')
    studio[0].state.commands.client = NoAPI()
    prepared = await configure(studio)
    checklist = prepared['checklist']
    assert checklist['scenario'] == 'user_problem'
    assert checklist['tag'] == 'user_scenario'
    assert checklist['source'] == 'scenario_registry'
    assert (await call(studio, 'list_trajectories', query='#user_scenario'))['total'] == 1
    assert sum(len(c['rules']) for c in checklist['checks'].values()) == 24
    assert 'Review semantics before signing off' in prepared['guide']
    assert prepared['sample']['replay']
    assert 'evaluations' not in prepared['sample'].get('metadata', {})
    assert 'endpoints' not in json.dumps(checklist)
    jsonschema.Draft202012Validator.check_schema(checklist['results_schema'])
    jsonschema.validate(results_for(checklist), checklist['results_schema'])
    small = await call(studio, 'review_trajectory', sample_id='test', include_source=False)
    assert 'sample' not in small and small['checklist'] == checklist
    assert (await call(studio, 'get_review_checklist', sample_id='test'))['checklist'] == checklist
    response = await submit(studio, prepared)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['validation_level'] == 2
    assert result['evaluation']['origin'] == 'mcp_checklist_review'
    assert result['evaluation']['checklist_hash'] == checklist['checklist_hash']
    assert result['evaluation']['report']['checks']['validate.reasoning']['valid']
    assert (await source(studio))['metadata']['evaluations'][-1] == result['evaluation']
    await call(studio, 'undo_edits')
    assert not (await source(studio))['metadata'].get('evaluations')


@pytest.mark.parametrize('fault', ['missing_category', 'missing_rule', 'extra_rule', 'inapplicable', 'needs_context', 'context_request', 'hash', 'revision'])
async def test_incomplete_or_stale_review_cannot_change_source(studio, fault):
    prepared = await configure(studio)
    results = results_for(prepared['checklist'])
    overrides = {}
    if fault == 'missing_category':
        results.pop('validate.tools')
    elif fault == 'missing_rule':
        results['validate.tools']['checks'].pop('causal_knowledge')
    elif fault == 'extra_rule':
        results['validate.tools']['checks']['invented'] = {'evidence': 'invented', 'status': 'pass', 'feedback': ''}
    elif fault in ('inapplicable', 'needs_context'):
        results['validate.tools']['checks']['causal_knowledge']['status'] = 'not_applicable' if fault == 'inapplicable' else 'needs_context'
    elif fault == 'context_request':
        results['validate.tools']['context_requests'] = ['window:a']
    elif fault == 'hash':
        overrides['checklist_hash'] = 'obsolete'
    elif fault == 'revision':
        overrides['expected_revision'] = prepared['revision'] - 1
    before = await source(studio)
    response = await submit(studio, prepared, results, **overrides)
    assert response.status_code in (409, 422), response.text
    assert await source(studio) == before


async def test_one_failed_rule_records_rejection_and_only_actionable_findings(studio):
    prepared = await configure(studio)
    results = results_for(prepared['checklist'])
    results['validate.tools']['checks']['causal_knowledge'].update(status='fail', feedback='Wait for the observed result.')
    response = await submit(studio, prepared, results)
    assert response.status_code == 200, response.text
    report = response.json()['evaluation']['report']
    assert response.json()['validation_level'] == -2
    assert len(report['feedback']) == 1
    assert '[causal_knowledge]' in report['feedback'][0]


async def test_repair_is_atomic_compiled_undoable_and_returns_checklist(studio):
    prepared = await configure(studio)
    await submit(studio, prepared)
    # Review after recording so the repair uses the current revision.
    prepared = await call(studio, 'review_trajectory', sample_id='test')
    before = await source(studio)
    args = {
        'sample_id': 'test', 'expected_revision': prepared['revision'],
        'checklist_hash': prepared['checklist']['checklist_hash'],
        'edits': [{'action': 'append', 'tick': '1', 'event': {
            'op': 'append', 'window': 'a', 'source': 'user', 'content': 'New evidence'
        }}],
    }
    dry = await call(studio, 'repair_trajectory', **args, dry_run=True)
    assert not dry['saved'] and await source(studio) == before
    repaired = await call(studio, 'repair_trajectory', **args)
    assert repaired['checklist'] == prepared['checklist'] and repaired['review_required']
    assert repaired['revision'] == prepared['revision'] + 1
    current = await source(studio)
    assert not current['metadata'].get('evaluations')
    assert current['metadata']['validation_level'] == 2
    assert current['replay']['1'][0]['content'] == 'New evidence'
    stale = await studio[1].post('/api/mcp/call', json={'name': 'repair_trajectory', 'arguments': args})
    assert stale.status_code == 409
    await call(studio, 'undo_edits')
    assert await source(studio) == before
    bad = {**args, 'expected_revision': studio[0].state.dataset['revision'], 'edits': [
        args['edits'][0], {'action': 'append', 'tick': '2', 'event': {
            'op': 'generate', 'window': 'missing', 'content': 'invalid'
        }}
    ]}
    rejected = await studio[1].post('/api/mcp/call', json={'name': 'repair_trajectory', 'arguments': bad})
    assert rejected.status_code == 422 and await source(studio) == before


async def test_snapshot_is_portable_and_preserved_when_registry_changes(studio):
    prepared = await configure(studio)
    snapshot = build_checklist(studio[0].state.scenario_runner.registry.scenarios['user_problem']).model_dump()
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {
        'scenario': 'user_problem', 'review_checklist': snapshot,
    }})
    definition = studio[0].state.scenario_runner.registry.scenarios['user_problem']
    definition.flow.steps['validate'].checks['reasoning'].rules['private_working_notes'].requirement = 'Changed after generation'
    unchanged = await call(studio, 'get_review_checklist', sample_id='test')
    assert unchanged['checklist']['checklist_hash'] == prepared['checklist']['checklist_hash']
    studio[0].state.scenario_runner.registry = ScenarioRegistry()
    read = await call(studio, 'review_trajectory', sample_id='test')
    assert read['checklist']['source'] == 'generation_snapshot'
    assert read['checklist']['checklist_hash'] == prepared['checklist']['checklist_hash']
    response = await submit(studio, read)
    assert response.status_code == 200, response.text
    restored = create_app(studio[0].state.dataset.store.path)
    reopened = await restored.state.commands.call('review_trajectory', {'sample_id': 'test'})
    assert reopened['checklist'] == read['checklist']


async def test_legacy_metadata_fallback_unknown_and_conflicting_scenarios(studio, tmp_path):
    prepared = await configure(studio)
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {
        'tags': ['coding'], 'scenario': 'user_problem',
    }})
    assert (await call(studio, 'get_review_checklist', sample_id='test'))['checklist']['scenario'] == 'user_problem'
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {
        'scenario': 'missing', 'tags': ['user_scenario'],
    }})
    response = await studio[1].post('/api/mcp/call', json={'name': 'review_trajectory', 'arguments': {'sample_id': 'test'}})
    assert response.status_code == 422 and response.json()['code'] == 'scenario_conflict'
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {
        'scenario': None, 'tags': ['unknown'],
    }})
    response = await studio[1].post('/api/mcp/call', json={'name': 'review_trajectory', 'arguments': {'sample_id': 'test'}})
    assert response.json()['code'] == 'scenario_not_found'


async def test_generated_tags_keep_all_randomized_dimensions_and_snapshot(compiler, tmp_path):
    runner = app_for(tmp_path, compiler, ScenarioClient(), legacy_reviews=False).state.scenario_runner
    runner.start('user_problem')
    await asyncio.wait_for(runner.job, 15)
    assert runner.status['phase'] == 'complete', list(runner.logs)
    sample = runner.builder.sample
    metadata = sample['metadata']
    assert 'user_scenario' in metadata['tags']
    expected = [t['name'] for t in metadata['context_tags'].values()] + [
        f"{role}:{t['name']}" for role, profile in metadata['roles'].items() for t in profile['tags'].values()
    ]
    assert set(metadata['tags']) == {'user_scenario', *expected}
    assert sum(len(c['rules']) for c in metadata['review_checklist']['checks'].values()) == 24


def test_custom_flow_tag_and_duplicate_tags_are_validated(tmp_path):
    root = write_scenario(tmp_path, {'tag': 'custom_scenario', 'steps': {'review': {
        'uses': 'remix.validate', 'prompt': 'review.md'
    }}}, {'review.md': 'Review this sample.'})
    scenario = ScenarioRegistry(root).scenarios['custom']
    assert build_checklist(scenario).tag == 'custom_scenario'
    shutil.copytree(root / 'nested' / 'custom', root / 'duplicate')
    with pytest.raises(ValueError, match='Duplicate scenario tag'):
        ScenarioRegistry(root)


async def test_multiple_scenario_tags_fail_instead_of_selecting_arbitrarily(studio, tmp_path):
    root = tmp_path / 'scenarios'
    shutil.copytree(EXAMPLES, root)
    shutil.copytree(root / 'user_problem', root / 'another')
    path = root / 'another' / 'flow.yaml'
    path.write_text(path.read_text().replace('tag: user_scenario', 'tag: another_scenario'))
    studio[0].state.scenario_runner.registry = ScenarioRegistry(root)
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {
        'tags': ['user_scenario', 'another_scenario'],
    }})
    response = await studio[1].post('/api/mcp/call', json={'name': 'review_trajectory', 'arguments': {'sample_id': 'test'}})
    assert response.status_code == 422 and response.json()['code'] == 'scenario_conflict'


async def test_legacy_checklist_results_remain_supported_and_tools_are_discoverable(studio, tmp_path):
    root = write_scenario(tmp_path, {'tag': 'custom_scenario', 'steps': {'review': {
        'uses': 'remix.validate', 'prompt': 'review.md'
    }}}, {'review.md': 'Review the source.'})
    studio[0].state.scenario_runner.registry = ScenarioRegistry(root)
    await call(studio, 'update_trajectory_metadata', sample_id='test', patch={'metadata': {'tags': ['custom_scenario']}})
    read = await call(studio, 'review_trajectory', sample_id='test')
    assert read['checklist']['checks']['review.review']['prompt'] == 'Review the source.'
    response = await submit(studio, read, {'review.review': {'valid': True, 'feedback': []}})
    assert response.status_code == 200, response.text
    catalog = {t['name']: t for t in studio[0].state.commands.tools()}
    for name in ('get_review_checklist', 'review_trajectory', 'repair_trajectory', 'record_trajectory_review'):
        jsonschema.Draft202012Validator.check_schema(catalog[name]['inputSchema'])
        assert not catalog[name]['annotations']['openWorldHint']
    assert not catalog['repair_trajectory']['annotations']['readOnlyHint']
