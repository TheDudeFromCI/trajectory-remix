"""Generation stays pending; every saved trajectory uses explicit shared review."""
import asyncio
import copy
import json

import pytest

from trajectory_remix.dataset import training_eligible
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.validation import ValidationRunner
from test_scenarios import review_output


class Reviewer:
    def __init__(self, *, valid=True, hold=False, malformed=False):
        self.valid, self.hold, self.malformed = valid, hold, malformed
        self.messages = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def complete(self, endpoint, messages, tools=None):
        self.messages.append(copy.deepcopy(messages))
        if self.hold:
            self.entered.set()
            await self.release.wait()
        if self.malformed:
            return {'content': '{"not":"a review"}'}
        return {'content': json.dumps(review_output(messages, self.valid, 'Fix the unsupported claim.'))}


def attach(app, reviewer):
    app.state.commands.client = reviewer
    validator = ValidationRunner(app.state.commands)
    app.state.runner.validation_runner = app.state.validation_runner = validator
    return validator


def test_bundled_choices_use_content_first_creation_without_inline_review():
    registry = ScenarioRegistry()
    assert [s['name'] for s in registry.listing()] == [
        'user_scenario', 'idle_scenario', 'kidra_large', 'kidra_medium', 'kidra_small']
    for scenario in registry.scenarios.values():
        assert scenario.flow.generation_mode == 'content-first'
        assert 'validate' not in scenario.flow.steps
        assert all(s.uses not in ('remix.validate', 'remix.novelty') for s in scenario.flow.steps.values())
    assert not registry.scenarios['user_scenario'].parent_name


@pytest.mark.parametrize('valid', [True, False])
async def test_explicit_shared_review_records_result_without_creating_content(studio, valid):
    app, _ = studio
    reviewer = Reviewer(valid=valid)
    validator = attach(app, reviewer)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    result = await validator.validate('test', record=True)
    assert result['valid'] is valid and result['recorded']
    assert len(result['checks']) == 4 and reviewer.messages
    after = app.state.dataset['samples'][0]
    assert after['replay'] == before['replay']
    assert after['metadata']['validation_level'] == (2 if valid else -2)
    assert not training_eligible(after)
    assert after['metadata']['evaluations'][-1]['origin'] == 'model_checklist_review'
    assert app.state.scenario_runner.snapshot is None
    with app.state.dataset.store.connect() as db:
        assert db.execute('SELECT count(*) FROM validation_jobs').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM generation_jobs').fetchone()[0] == 0


async def test_mcp_semantic_validation_uses_same_service_without_recording(studio):
    app, _ = studio
    reviewer = Reviewer()
    attach(app, reviewer)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    result = await app.state.commands.call('validate_trajectory', {'sample_id': 'test', 'semantic': True})
    assert result['semantic']['valid']
    assert reviewer.messages
    assert app.state.dataset['samples'][0] == before


async def test_validation_cancel_and_resume_never_approve_unseen_evidence(studio):
    app, _ = studio
    reviewer = Reviewer(hold=True)
    validator = attach(app, reviewer)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    validator.start('test')
    await asyncio.wait_for(reviewer.entered.wait(), 5)
    with pytest.raises(ValueError, match='cancel trajectory validation'):
        app.state.scenario_runner.start(None)
    with pytest.raises(ValueError, match='cancel trajectory validation'):
        app.state.scenario_runner.resume_saved('test')
    await validator.stop()
    assert validator.status['phase'] == 'cancelled'
    assert app.state.dataset['samples'][0] == before
    reviewer.hold = False
    validator.start('test', resume=True)
    result = await validator.job
    assert result['valid'] and result['recorded']


async def test_source_or_custom_requirement_change_prevents_stale_result(studio):
    app, _ = studio
    reviewer = Reviewer(hold=True)
    validator = attach(app, reviewer)
    validator.start('test')
    await asyncio.wait_for(reviewer.entered.wait(), 5)
    config = copy.deepcopy(app.state.dataset['config'])
    config['review_guidance'] = {'guide': 'A new requirement arrived during review.'}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    reviewer.release.set()
    with pytest.raises(ValueError, match='changed during validation'):
        await validator.job
    assert validator.status['phase'] == 'incomplete'
    assert app.state.dataset['samples'][0].get('metadata', {}).get('validation_level', -3) != 2


async def test_malformed_validation_retains_journal_without_a_verdict(studio):
    app, _ = studio
    validator = attach(app, Reviewer(malformed=True))
    before = copy.deepcopy(app.state.dataset['samples'][0])
    with pytest.raises(Exception, match='review|checks|schema'):
        await validator.validate('test', record=True)
    assert app.state.dataset['samples'][0] == before
    assert validator.status['phase'] == 'incomplete'
    assert validator.snapshot['request_metrics']
    assert all(r['raw_output'] == '{"not":"a review"}' for r in validator.snapshot['content_requests'])


async def test_explicit_validation_api_and_retired_task_api(studio):
    app, client = studio
    reviewer = Reviewer()
    # The app endpoint closes over its original validator instance.
    app.state.commands.client = reviewer
    app.state.validation_runner.client = reviewer
    response = await client.post('/api/run', json={})
    assert response.status_code == 410
    started = await client.post('/api/validation/run', json={'sample_id': 'test'})
    assert started.status_code == 202, started.text
    await app.state.validation_runner.job
    status = await client.get('/api/validation/status')
    assert status.json()['phase'] == 'complete' and status.json()['result']['recorded']
    assert (await client.get('/api/state')).json()['validation_status']['phase'] == 'complete'


async def test_creation_aliases_use_one_pending_user_flow_with_no_validator(compiler, tmp_path, monkeypatch):
    from test_scenarios import app_for
    from test_scenario_variations import NovalClient, FourByteTokenizer
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: FourByteTokenizer())
    fake = NovalClient(tools=True, valid=False)
    app = app_for(tmp_path, compiler, fake, scenarios=None, legacy_reviews=False)
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'] = [config['endpoints'][0]]
    config['endpoints'][0].update(inference_tokenizer='fixture', max_tokens=1024)
    config.setdefault('automatic', {})['validate'] = True  # Imported flag cannot enable inline validation.
    config['tasks'] = [json.loads((__import__('pathlib').Path(__file__).parent / 'fixtures/legacy-generation-config.json').read_text())['tasks'][-1]]
    config['tasks'][0]['endpoint'] = 'retired-missing-endpoint'
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner = app.state.scenario_runner
    for name in (None, 'user_problem', 'user_scenario_noval', 'user_scenario'):
        runner.start(name)
        await runner.job
        assert runner.status['phase'] == 'complete', list(runner.logs)
        sample = app.state.dataset['samples'][-1]
        assert sample['metadata']['scenario'] == 'user_scenario'
        assert sample['metadata']['validation_level'] == 0
        assert not sample['metadata'].get('evaluations')
    assert len(fake.messages) == 16
    assert runner.status['accepted'] == 4
    assert not app.state.validation_runner.snapshot
    with pytest.raises(ValueError, match='Unknown scenario'):
        runner.start('content_first')


async def test_config_accepts_generator_only_and_ui_has_no_inline_validation(studio):
    app, client = studio
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'] = [config['endpoints'][0]]
    response = await client.put('/api/config', json={'config': config})
    assert response.status_code == 200, response.text
    html = (await client.get('/')).text
    assert 'Validate Trajectory' in html and 'id="validate-trajectory"' in html
    assert 'Automatic mode' not in html
    assert [s['name'] for s in (await client.get('/api/state')).json()['scenarios']] == [
        'user_scenario', 'idle_scenario', 'kidra_large', 'kidra_medium', 'kidra_small']
    # A missing optional validator is reported only when explicitly requested.
    response = await client.post('/api/validation/run', json={'sample_id': 'test'})
    assert response.status_code == 422 and 'not configured' in response.json()['detail']


async def test_synchronous_validation_is_readonly_when_requested_and_rejects_unsaved_source(studio):
    app, client = studio
    reviewer = Reviewer()
    app.state.validation_runner.client = reviewer
    before = copy.deepcopy(app.state.dataset['samples'][0])
    response = await client.post('/api/validate', json={'index': 0, 'record': False})
    assert response.status_code == 200, response.text
    assert response.json()['valid'] and not response.json()['recorded']
    assert app.state.dataset['samples'][0] == before
    proposed = copy.deepcopy(before)
    proposed.setdefault('metadata', {})['new_unsaved_requirement'] = True
    response = await client.post('/api/validate', json={'sample': proposed})
    assert response.status_code == 422 and 'Save the submitted' in response.json()['detail']
    assert len(reviewer.messages) == 4


async def test_original_review_snapshot_survives_canonical_renaming(studio):
    from test_scenarios import EXAMPLES
    from trajectory_remix.trajectory_review import build_checklist
    app, _ = studio
    old = build_checklist(ScenarioRegistry(EXAMPLES).scenarios['user_scenario_noval']).model_dump()
    await app.state.commands.call('update_trajectory_metadata', {'sample_id': 'test', 'patch': {
        'metadata': {'tags': ['user_scenario'], 'scenario': 'user_scenario_noval', 'scenario_family': 'user_problem', 'review_checklist': old}}})
    prepared = await app.state.commands.call('get_review_checklist', {'sample_id': 'test'})
    assert prepared['checklist']['scenario'] == 'user_scenario_noval'
    assert len(prepared['checklist']['checks']['validate.tools']['rules']) == len(old['checks']['validate.tools']['rules'])
    validator = attach(app, Reviewer())
    result = await validator.validate('test', record=True)
    assert result['valid']
    assert app.state.dataset['samples'][0]['metadata']['review_checklist'] == old


async def test_old_scenario_guidance_applies_to_canonical_generation(studio):
    app, _ = studio
    config = copy.deepcopy(app.state.dataset['config'])
    config['review_guidance'] = {'scenarios': {'user_problem': {'guide': 'Retained old responsibility.'},
                                             'user_scenario_noval': {'checklist': 'Retained fast-flow requirement.'}}}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    bundle = app.state.commands.get_guidance(scenario='user_scenario')
    prompt = bundle['checklist']['checks']['$custom.scenario']['prompt']
    assert 'Retained old responsibility' in prompt and 'Retained fast-flow requirement' in prompt


async def test_explicit_review_does_not_mutate_original_generation_metrics(compiler, tmp_path, monkeypatch):
    from test_idle_scenario import IdleClient, setup_idle
    app, generator = setup_idle(tmp_path, compiler, monkeypatch, IdleClient('quiet'))
    generator.start('idle_scenario')
    await generator.job
    sample = copy.deepcopy(app.state.dataset['samples'][0])
    validator = attach(app, Reviewer())
    await validator.validate(sample['id'], record=True, endpoint='generator')
    after = app.state.dataset['samples'][0]
    assert after['metadata']['generation_record'] == sample['metadata']['generation_record']
    assert after['replay'] == sample['replay']
    assert generator.status['model_requests'] == 2


@pytest.mark.parametrize('context_kind', ['unknown', 'already_supplied'])
async def test_unresolved_evidence_is_bounded_and_never_recorded(studio, context_kind):
    class MissingEvidence(Reviewer):
        async def complete(self, endpoint, messages, tools=None):
            self.messages.append(copy.deepcopy(messages))
            payload = json.loads(messages[1]['content'])
            wanted = 'missing-unit' if context_kind == 'unknown' else payload['events'][0]['id']
            return {'content': json.dumps(review_output(messages, context_requests=[wanted]))}
    app, _ = studio
    reviewer = MissingEvidence()
    validator = attach(app, reviewer)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    with pytest.raises(Exception, match='evidence|context|unit'):
        await validator.validate('test', record=True)
    assert len(reviewer.messages) == 3
    assert validator.status['phase'] == 'incomplete'
    assert app.state.dataset['samples'][0] == before
    assert validator.snapshot['content_requests'][-1]['raw_output']


async def test_oversized_explicit_review_never_truncates_or_calls_model(studio, monkeypatch):
    from test_scenario_variations import FourByteTokenizer
    monkeypatch.setattr("trajectory_remix.content_first.inference_tokenizer", lambda _: FourByteTokenizer())
    app, _ = studio
    config = copy.deepcopy(app.state.dataset['config'])
    config['validation'] = {'endpoint': 'validator', 'max_input_tokens': 512}
    config['endpoints'][1]['inference_tokenizer'] = 'fixture'
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    reviewer = Reviewer()
    validator = attach(app, reviewer)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    with pytest.raises(ValueError, match='budget|fit|token'):
        await validator.validate('test', record=True)
    assert not reviewer.messages
    assert validator.snapshot['sample'] == before
    assert app.state.dataset['samples'][0] == before


async def test_shared_validation_counts_model_template_completion_and_reserve(studio, monkeypatch):
    from test_scenario_variations import FourByteTokenizer
    from trajectory_remix.content_first import RequestBudget
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: FourByteTokenizer())
    app, _ = studio
    config = copy.deepcopy(app.state.dataset['config'])
    # Reuse the generator tokenizer only for the same served model.
    config['endpoints'][0].update(inference_tokenizer='fixture', context_size=8192)
    config['endpoints'][1].update(context_size=8192, max_tokens=1024)
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    reviewer = Reviewer()
    validator = attach(app, reviewer)
    assert (await validator.validate('test'))['valid']
    assert validator.endpoint['inference_tokenizer'] == 'fixture'
    for messages, request in zip(reviewer.messages, validator.snapshot['content_requests'], strict=True):
        bounded = RequestBudget(validator.endpoint, config['content_first']).check(
            messages, response_format=request['response_format'])
        assert bounded['request_input_tokens_preflight'] + 1024 + 256 <= 8192
        assert request['budget'] == bounded
    # A different model must not inherit the generator tokenizer.
    config['endpoints'][1]['model'] = 'another-model'
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    validator.start('test')
    assert validator.request_budget('validator') is None
    await validator.job


async def test_missing_chat_reply_overrides_positive_model_review_but_idle_needs_no_chat(studio):
    app, _ = studio
    for tick, event in [('0', {'op': 'open', 'window': 'chat', 'window-type': 'chat'}),
                        ('1', {'op': 'append', 'window': 'chat', 'source': 'user', 'content': 'Hello'})]:
        await app.state.commands.call('create_event', {'sample_id': 'test', 'tick': tick, 'event': event})
    validator = attach(app, Reviewer())
    result = await validator.validate('test', record=True)
    assert not result['valid']
    assert 'chat tool reply' in ' '.join(result['feedback'])
    assert result['validation_level'] == -2


async def test_restart_resume_reuses_only_finished_matching_evidence_groups(studio):
    from trajectory_remix.server import create_app
    class HoldSecond(Reviewer):
        async def complete(self, endpoint, messages, tools=None):
            self.messages.append(copy.deepcopy(messages))
            if len(self.messages) == 2:
                self.entered.set()
                await self.release.wait()
            return {'content': json.dumps(review_output(messages))}
    app, _ = studio
    reviewer = HoldSecond()
    validator = attach(app, reviewer)
    validator.start('test')
    await asyncio.wait_for(reviewer.entered.wait(), 5)
    await validator.stop()
    reopened = create_app(app.state.dataset.store.path, client=reviewer)
    resumed = reopened.state.validation_runner
    resumed.start('test', resume=True)
    result = await resumed.job
    assert result['recorded']
    assert len(reviewer.messages) == 5  # One finished group is reused; interrupted group is retried.


@pytest.mark.parametrize('field,value', [('record','false'),('resume',1),('endpoint',1),('sample_id',None),('sample_id','')])
async def test_validation_rejects_ambiguous_selection_flags(studio, field, value):
    validator = attach(studio[0], Reviewer())
    options = {'sample_id': 'test', field: value}
    with pytest.raises(ValueError):
        validator.start(**options)
    assert not validator.status['running']
