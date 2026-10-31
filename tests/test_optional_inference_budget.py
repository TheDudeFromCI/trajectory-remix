"""Optional inference tokenizers never block creation or substitute hard estimates."""
import copy

import httpx
import pytest

from test_idle_scenario import IdleClient
from test_scenario_variations import NovalClient
from test_scenarios import app_for
from test_standalone_validation import Reviewer, attach


@pytest.mark.parametrize('scenario,request_count', [('user_scenario', 4), ('idle_scenario', 5)])
@pytest.mark.parametrize('tokenizer', ['absent', 'blank', 'missing-files', 'missing-dependency', 'missing-template'])
async def test_default_flows_ignore_budgets_without_available_tokenizer(compiler, tmp_path, monkeypatch, scenario, request_count, tokenizer):
    def unavailable(path):
        assert path == 'unavailable-tokenizer'
        if tokenizer == 'missing-dependency':
            raise ImportError('transformers is not installed')
        if tokenizer == 'missing-template':
            raise ValueError('Inference tokenizer must include the endpoint model chat_template')
        raise OSError('Tokenizer files are missing')
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', unavailable)
    fake = NovalClient(tools=True) if scenario == 'user_scenario' else IdleClient()
    app = app_for(tmp_path, compiler, fake, scenarios=None, legacy_reviews=False)
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'] = [config['endpoints'][0]]
    config['endpoints'][0].update(context_size=1)
    if tokenizer != 'absent':
        config['endpoints'][0]['inference_tokenizer'] = '' if tokenizer == 'blank' else 'unavailable-tokenizer'
    config['content_first'] = {'context_budget': 1, 'reserve_tokens': 256}
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    runner = app.state.scenario_runner
    next(iter(runner.registry.scenarios[scenario].flow.steps.values())).max_input_tokens = 1
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver', headers={'X-Remix-Token': app.state.csrf}) as client:
        response = await client.put('/api/config', json={'config': config})
        assert response.status_code == 200, response.text
        assert (await client.post('/api/scenarios/run', json={'scenario': scenario})).status_code == 200
        await runner.job
    assert runner.status['phase'] == 'complete', list(runner.logs)
    assert len(fake.messages) == request_count
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    assert not compiler.compile(sample)['diagnostics']
    requests = sample['metadata']['generation_record']['requests']
    assert len(requests) == request_count
    reason = 'inference_tokenizer_unconfigured' if tokenizer in ('absent', 'blank') else 'inference_tokenizer_unavailable'
    assert all(r['budget'] == {'budget_enforced': False, 'budget_skipped_reason': reason} for r in requests)
    assert all(r['metrics']['token_source'] == 'estimated' and r['metrics']['token_estimator'] == 'training-tokenizer' for r in requests)
    assert all('prompt_tokens_preflight' not in r['budget'] for r in requests)
    if scenario == 'user_scenario':
        assert '2 + 2' in fake.messages[2][1]['content']  # Required user evidence remains intact.


async def test_explicit_validation_skips_local_estimated_limits_without_tokenizer(studio):
    app, _ = studio
    config = copy.deepcopy(app.state.dataset['config'])
    config['validation']['max_input_tokens'] = 512
    config['endpoints'][1]['context_size'] = 1
    config['content_first']['context_budget'] = 1
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    reviewer = Reviewer()
    validator = attach(app, reviewer)
    before = copy.deepcopy(app.state.dataset['samples'][0])
    assert (await validator.validate('test', record=False))['valid']
    assert reviewer.messages
    assert app.state.dataset['samples'][0] == before
    assert all(not r['budget']['budget_enforced'] for r in validator.snapshot['content_requests'])
