"""Shared user-scenario inputs and fast generation without local review."""
import asyncio
import copy
import json
from pathlib import Path

import pytest
import yaml

from trajectory_remix.content_first import public_tool_contracts
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.trajectory_review import build_checklist
from test_content_first import ByteChatTokenizer
from test_scenarios import EXAMPLES, app_for, ScenarioClient


class FourByteTokenizer(ByteChatTokenizer):
    # Synthetic inference tokenizer; distinct from the training tokenizer.
    def apply_chat_template(self, *args, **kwargs):
        return super().apply_chat_template(*args, **kwargs)[::4]

    def encode(self, text, **kwargs):
        return super().encode(text, **kwargs)[::4]


class NovalClient(ScenarioClient):
    async def complete(self, endpoint, messages, tools=None):
        prompt = messages[0]['content']
        if prompt.startswith('Plan Kidra'):
            self.messages.append(copy.deepcopy(messages))
            assert 'success_criteria' not in messages[1]['content']
            assert 'author-only' not in messages[1]['content']
            return {'content': json.dumps({'reasoning': 'Verify the stated arithmetic before answering.',
                'calls': [[{'tool': 'calculator', 'arguments': {'expression': '2 + 2'}}]] if self.tools else []})}
        if prompt.startswith('Write Kidra\'s private reasoning and next'):
            self.messages.append(copy.deepcopy(messages))
            self.assessments += 1
            return {'content': json.dumps({'reasoning': 'Two pairs total four; the answer is supported.',
                'response': 'The answer is 4.', 'resolved': self.assessments >= self.turns,
                'next_exchange': '' if self.assessments >= self.turns else 'Ask for a concrete example.'})}
        if prompt.startswith(('Solve', 'Judge', 'Validate', 'Decide')):
            pytest.fail('Fast scenario must not call reference solution, novelty, assessment or validation models')
        return await super().complete(endpoint, messages, tools)


def test_variation_inherits_the_exact_generation_inputs_and_checklist():
    registry = ScenarioRegistry(EXAMPLES)
    source = registry.scenarios['user_problem']
    fast = registry.scenarios['user_scenario_noval']
    assert fast.flow.tag == source.flow.tag == 'user_scenario'
    assert fast.family == source.name
    assert fast.flow.system_prompt == source.flow.system_prompt
    assert fast.flow.tags == source.flow.tags and len(fast.flow.tags['domain']) >= 40
    assert fast.flow.roles == source.flow.roles
    assert fast.flow.steps['idea'].inputs == source.flow.steps['idea'].inputs
    assert fast.prompts['idea.md'] == source.prompts['idea.md']
    assert fast.prompts['user.md'] == source.prompts['user.md']
    assert build_checklist(fast).checks == build_checklist(source).checks
    assert list(fast.flow.steps) == ['idea', 'environment', 'conversation', 'validate']
    listings = {item['name']: item for item in registry.listing()}
    assert listings[fast.name]['tag_pools'] == listings[source.name]['tag_pools']
    assert listings[fast.name]['variation_of'] == source.name


@pytest.mark.parametrize('turns,tools', [(1, False), (1, True), (2, True)])
async def test_fast_user_scenario_retains_all_conditions_and_needs_three_calls_per_turn(compiler, tmp_path, monkeypatch, turns, tools):
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: FourByteTokenizer())
    fake = NovalClient(turns=turns, tools=tools)
    app = app_for(tmp_path, compiler, fake, legacy_reviews=False)
    runner = app.state.scenario_runner
    config = copy.deepcopy(app.state.dataset['config'])
    config['tasks'] = []
    config['endpoints'] = [config['endpoints'][0]]
    config['endpoints'][0].update(inference_tokenizer='four-byte-fixture', max_tokens=1024)
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    # The same manual selections accepted by user_problem are accepted unchanged.
    chosen = {scope: {dim: options[-1].name for dim, options in pools.items()}
              for scope, pools in {'scenario': runner.registry.scenarios['user_problem'].flow.tags,
                                    **{role: profile.tags for role, profile in runner.registry.scenarios['user_problem'].flow.roles.items()}}.items()}
    runner.start('user_scenario_noval', tags=chosen)
    await asyncio.wait_for(runner.job, 15)
    assert runner.status['phase'] == 'complete', list(runner.logs)
    sample = app.state.dataset['samples'][0]
    assert len(fake.messages) == 1 + 3 * turns
    assert sample['metadata']['scenario'] == 'user_scenario_noval'
    assert sample['metadata']['scenario_family'] == 'user_problem'
    assert 'user_scenario' in sample['metadata']['tags']
    assert sample['metadata']['context_tags']['domain']['name'] == chosen['scenario']['domain']
    assert sample['metadata']['roles']['user']['tags']['behavior']['name'] == chosen['user']['behavior']
    assert sample['metadata']['validation_level'] == 0
    assert len(sample['metadata']['review_checklist']['checks']) == 4
    assert all(r['budget']['context_budget'] == 8192 for r in sample['metadata']['generation_record']['requests'])
    plan = compiler.compile(sample)
    assert sum(c['tool'] == 'chat' for c in plan['tool_calls']) == turns
    assert sum(c['tool'] == 'calculator' for c in plan['tool_calls']) == (turns if tools else 0)
    if tools:
        planning = next(e for e in plan['events'] if e.get('op') == 'generate' and e.get('window', '').startswith('reasoning-'))
        first_call = next(c for c in plan['tool_calls'] if c['tool'] == 'calculator')
        assert first_call['request_start'] == planning['start']
    assert all(w['close'] is not None for w in plan['windows'])
    prepared = await app.state.commands.call('review_trajectory', {'sample_id': sample['id']})
    assert prepared['checklist']['scenario'] == 'user_scenario_noval'
    assert prepared['checklist']['source'] == 'generation_snapshot'
    runner.registry.scenarios.pop('user_scenario_noval')
    archived = await app.state.commands.call('get_review_checklist', {'sample_id': sample['id']})
    assert archived['checklist'] == prepared['checklist']


async def test_shared_tag_keeps_legacy_tag_only_reviews_unambiguous(studio):
    app, _ = studio
    app.state.scenario_runner.registry = ScenarioRegistry(EXAMPLES)
    await app.state.commands.call('update_trajectory_metadata', {'sample_id': 'test', 'patch': {'metadata': {'tags': ['user_scenario']}}})
    prepared = await app.state.commands.call('get_review_checklist', {'sample_id': 'test'})
    assert prepared['checklist']['scenario'] == 'user_problem'


def test_inheritance_tracks_parent_inputs_and_rejects_cycles_and_unrelated_shared_tags(tmp_path):
    root = tmp_path / 'scenarios'
    for name, definition in {
        'base': {'tag': 'shared', 'tags': {'domain': [{'name': 'new domain'}]}, 'steps': {'idea': {'uses': 'llm.content', 'prompt': 'idea.md'}}},
        'fast': {'extends': 'base', 'generation_mode': 'content-first'},
    }.items():
        folder = root / name
        folder.mkdir(parents=True)
        (folder / 'flow.yaml').write_text(yaml.safe_dump(definition))
    (root / 'base' / 'idea.md').write_text('Generate content.')
    registry = ScenarioRegistry(root)
    assert registry.scenarios['fast'].flow.tags == registry.scenarios['base'].flow.tags
    (root / 'base' / 'flow.yaml').write_text('extends: fast\n')
    with pytest.raises(ValueError, match='cycle'):
        ScenarioRegistry(root)


def test_tool_contract_compaction_keeps_argument_validation(compiler):
    from jsonschema import Draft202012Validator
    contracts = public_tool_contracts(compiler.tools.schemas())
    for schema in compiler.tools.schemas():
        name = schema['function']['name']
        assert contracts[name]['purpose'] == schema['function']['description']
        Draft202012Validator.check_schema(contracts[name]['arguments'])
        assert contracts[name]['arguments'].get('required') == schema['function']['parameters'].get('required')
    assert 'chat' in contracts and 'calculator' in contracts


def test_tool_contracts_preserve_argument_names_and_literal_schema_values():
    schema = {'type': 'object', 'description': 'annotation', 'properties': {
        'description': {'type': 'string', 'description': 'documentation'},
        'title': {'const': {'description': 'literal constraint'}},
    }, 'required': ['description', 'title'], 'additionalProperties': False}
    result = public_tool_contracts([{'function': {'name': 'custom', 'description': 'Purpose', 'parameters': schema}}])['custom']['arguments']
    assert set(result['properties']) == {'description', 'title'}
    assert 'description' not in result
    assert result['properties']['title']['const'] == {'description': 'literal constraint'}
