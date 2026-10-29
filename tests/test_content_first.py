"""Bounded content requests, causal construction, durable recovery and external review."""
import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from trajectory_remix.content_first import RequestBudget, dataset_generation_metrics
from trajectory_remix.dataset import training_eligible
from trajectory_remix.scenarios import ScenarioRegistry
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore
from trajectory_remix.training import query_batch
from test_scenarios import app_for, finish, ScenarioClient

EXAMPLES = Path(__file__).parent / 'fixtures/retired-scenarios'


class ByteChatTokenizer:
    """Synthetic inference model with one token per byte and explicit chat framing."""
    chat_template = 'test-byte-chat-v1'

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True, return_dict=False):
        return list((json.dumps(messages, ensure_ascii=False) + '\nassistant:').encode())

    def encode(self, text, add_special_tokens=False):
        return list(text.encode())


@pytest.fixture(autouse=True)
def model_tokenizer(monkeypatch):
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda path: ByteChatTokenizer())


class ContentClient:
    def __init__(self, *, fail=None, hold=False):
        self.messages = []
        self.fail = fail
        self.hold = hold
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = self.peak = 0

    async def complete(self, endpoint, messages, tools=None):
        self.messages.append(copy.deepcopy(messages))
        prompt = messages[0]['content']
        if prompt.startswith('Create a fictional'):
            output = {'problem': 'An invoice claims 2 + 2 is 5. Check invoice.txt and the sum.',
                      'situation': 'The invoice may be missing.', 'success_criteria': 'Calculate 4 and report any missing evidence.', 'files': []}
        elif prompt.startswith(('Analyze', 'Independently', 'Choose useful')):
            self.active += 1
            self.peak = max(self.active, self.peak)
            self.entered.set()
            if self.hold:
                await self.release.wait()
            await asyncio.sleep(.01)
            self.active -= 1
            if prompt.startswith('Choose useful'):
                output = {'calls': [{'tool': 'calculator', 'arguments': {'expression': '2 + 2'}},
                                    {'tool': 'file_read', 'arguments': {'path': 'invoice.txt'}}]}
            else:
                return {'content': 'The arithmetic needs verification.' if prompt.startswith('Analyze') else 'The invoice may be missing; do not assume its contents.'}
        elif prompt.startswith('Integrate actual'):
            assert 'false' in messages[1]['content']  # genuine missing-file failure
            output = {'reasoning': 'Calculator confirms 4. The file could not be read.',
                      'response': '2 + 2 = 4. I could not read invoice.txt, so its contents remain unverified.'}
            if self.fail == 'schema':
                output = {'unexpected': 'expensive proposal, retained'}
            elif self.fail == 'control':
                output['reasoning'] = 'bad <|eos|> syntax'
            elif self.fail == 'length':
                return {'content': '{"reasoning": "partial expensive', '_finish_reason': 'length'}
        elif prompt.startswith('Validate'):
            return {'content': json.dumps({'valid': True, 'feedback': []})}
        else:
            raise AssertionError(prompt[:100])
        return {'content': json.dumps(output)}


def setup(tmp_path, compiler, client, *, mode='content-first'):
    app = app_for(tmp_path, compiler, client)
    runner = app.state.scenario_runner
    assert 'user_problem' in runner.registry.scenarios
    runner.registry.scenarios['content_first'].flow.generation_mode = mode
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'][0].update(inference_tokenizer='synthetic-byte-model', max_tokens=1024)
    config['single_generation'] = {'max_attempts': 1, 'api_retries': 0}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    return app, runner


def test_entire_request_budget_and_reasoning_allowance():
    endpoint = {'model': 'different-model', 'inference_tokenizer': 'different-tokenizer', 'max_tokens': 16}
    messages = [{'role': 'system', 'content': 'café'}]
    prompt = len(ByteChatTokenizer().apply_chat_template(messages))
    budget = RequestBudget(endpoint, {'context_budget': prompt + 16 + 8, 'reserve_tokens': 8})
    assert budget.check(messages)['context_utilization_reserved'] == 1
    assert budget.check(messages)['prompt_tokens_preflight'] == prompt
    with pytest.raises(ValueError, match='required context was retained'):
        budget.check(messages + [{'role': 'user', 'content': 'x'}])
    with pytest.raises(ValueError, match='budget'):
        budget.check(messages, prompt - 1)
    assert messages == [{'role': 'system', 'content': 'café'}]
    assert RequestBudget(endpoint, {}).context == 8192
    assert RequestBudget({**endpoint, 'context_size': 4096}, {}).context == 4096
    assert RequestBudget(endpoint, {'context_budget': 16384}).context == 16384


async def test_parallel_content_tools_causality_lifecycle_and_masks(compiler, tmp_path):
    fake = ContentClient()
    app, runner = setup(tmp_path, compiler, fake)
    runner.start('content_first', max_parallel=3)
    await finish(runner)
    sample = app.state.dataset['samples'][0]
    assert fake.peak == 3
    assert len(fake.messages) == 5
    assert not any(m[0]['content'].startswith('Validate') for m in fake.messages)
    assert not training_eligible(sample) and sample['metadata']['validation_level'] == 0
    assert sample['metadata']['concept']['success_criteria']
    plan = compiler.compile(sample)
    assert all(w['close'] is not None for w in plan['windows'])
    a = next(e for e in plan['events'] if e.get('window') == 'arithmetic' and e.get('op') == 'generate')
    b = next(e for e in plan['events'] if e.get('window') == 'uncertainty' and e.get('op') == 'generate')
    assert a['start'] == b['start']
    assert a['start'] <= b['end'] and b['start'] <= a['end']
    calls = plan['tool_calls']
    assert [c['tool'] for c in calls] == ['calculator', 'file_read', 'chat']
    assert calls[0]['request_start'] == calls[1]['request_start'] == a['start']
    assert not calls[1]['outcome']['ok']
    for call in calls:
        request_rows = [r for r in plan['rows'] if r['event'] == call['path'] + '.request']
        assert request_rows and all(r['loss_weight'] > 0 for r in request_rows)
    assert all(r['loss_weight'] == 0 for r in plan['rows'] if r['phase'] == 'B')
    assert any(r['token_id'] == compiler.ids['noop'] for r in plan['rows'])
    assert any(r['token_id'] == compiler.ids['bos'] for r in plan['rows'])
    # Sample queries from both parallel windows and the delivered reply.
    selected = [r for r in plan['rows'] if r['loss_weight'] > 0][::37]
    batch = query_batch(plan, selected)
    memory = [r for r in plan['rows'] if r['durable']]
    targets = selected
    for q, mask in zip(targets, batch['attention_allowed']):
        assert all(not allowed or (k['tick'] <= q['tick'] and not (k['tick'] == q['tick'] and k['phase'] == 'D')) for k, allowed in zip(memory, mask))
    for messages in fake.messages[1:4]:
        assert 'could not read' not in messages[1]['content']
        assert 'observations' not in messages[1]['content']
    record = sample['metadata']['generation_record']
    assert record['metrics']['request_count'] == 5
    assert record['metrics']['constructed_drafts'] == 1
    assert all(r['raw_output'] and r['state'] == 'complete' for r in record['requests'])
    assert all(r['metrics']['context_utilization_reserved'] <= 1 for r in record['requests'])
    assert record['requirements']['prompts']['review.md']
    assert sample['metadata']['review_checklist']['checks']
    reopened = create_app(app.state.dataset.store.path, scenarios_dir=None)
    assert reopened.state.dataset['samples'][0] == sample
    analytics = dataset_generation_metrics([sample])['content-first']
    assert analytics['retained_candidates'] == 1 and analytics['approved_samples'] == 0
    assert analytics['usable_throughput_per_second'] == 0


@pytest.mark.parametrize('failure', ['schema', 'control', 'length'])
async def test_failed_proposals_are_durable_and_resumable(compiler, tmp_path, failure):
    fake = ContentClient(fail=failure)
    app, runner = setup(tmp_path, compiler, fake)
    runner.start('content_first', max_parallel=3)
    await runner.job
    assert runner.status['phase'] == 'rejected'
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    assert runner.can_resume(sample['id'])
    record = sample['metadata']['generation_record']
    assert record['metrics']['unsuccessful_drafts'] == 1
    assert record['failures']
    assert record['requests'][-1]['raw_output']
    if failure == 'control':
        assert 'bad <|eos|>' in json.dumps(record['failures'])
    if failure == 'control':
        # The valid schema response is cached; an external reviewer repairs the
        # saved construction proposal rather than paying to regenerate it.
        assert record['failures'][-1]['sample']
        return
    reopened = create_app(app.state.dataset.store.path, client=ContentClient(), scenarios_dir=EXAMPLES)
    resumed = reopened.state.scenario_runner
    resumed.resume_saved(sample['id'])
    await finish(resumed)
    repaired = reopened.state.dataset['samples'][0]
    assert len(repaired['metadata']['generation_record']['requests']) == 6
    assert repaired['metadata']['generation_record']['failures']  # history survives
    assert repaired['metadata']['validation_level'] == 0


async def test_context_failure_never_calls_model_or_drops_inputs(compiler, tmp_path):
    fake = ContentClient()
    app, runner = setup(tmp_path, compiler, fake)
    config = copy.deepcopy(app.state.dataset['config'])
    config['content_first'] = {'context_budget': 1024, 'reserve_tokens': 256}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('content_first')
    await runner.job
    assert not fake.messages
    sample = app.state.dataset['samples'][0]
    assert 'budget exceeded' in json.dumps(sample['metadata']['generation_record']['failures'])
    assert sample['metadata']['generation_record']['requirements']['flow']['tags']


async def test_cancellation_keeps_completed_siblings_and_resume(compiler, tmp_path):
    fake = ContentClient(hold=True)
    app, runner = setup(tmp_path, compiler, fake)
    runner.start('content_first', max_parallel=3)
    await fake.entered.wait()
    await runner.stop()
    sample = app.state.dataset['samples'][0]
    assert runner.status['phase'] == 'cancelled'
    assert runner.can_resume(sample['id'])
    assert sample['metadata']['generation_record']['requests'][0]['state'] == 'complete'
    fake.hold = False
    runner.resume_saved(sample['id'])
    await finish(runner)
    assert sum(m[0]['content'].startswith('Create a fictional') for m in fake.messages) == 1


async def test_existing_mode_and_original_scenario_still_validate(compiler, tmp_path):
    fake = ContentClient()
    app, runner = setup(tmp_path, compiler, fake, mode='existing')
    runner.start('content_first')
    await finish(runner)
    assert not any(m[0]['content'].startswith('Validate') for m in fake.messages)
    assert app.state.dataset['samples'][0]['metadata']['validation_level'] == 0
    assert len(app.state.dataset['samples'][0]['metadata']['generation_metrics']) == 5


async def test_exact_huggingface_template(tmp_path, monkeypatch):
    transformers = pytest.importorskip('transformers')
    pytest.importorskip('jinja2')
    from tokenizers import Tokenizer
    # Exercise the real local loader despite the synthetic-model fixture.
    import trajectory_remix.content_first as module
    # The implementation is independently loaded so monkeypatch cannot mask it.
    import runpy
    loader = runpy.run_path(str(Path(module.__file__)))['inference_tokenizer']
    fast = transformers.PreTrainedTokenizerFast(tokenizer_object=Tokenizer.from_file(str(Path(__file__).parents[1] / 'examples/demo-tokenizer.json')))
    fast.chat_template = "{% for m in messages %}{{ m['role'] }}:{{ m['content'] }}\n{% endfor %}{% if add_generation_prompt %}assistant:{% endif %}"
    fast.save_pretrained(tmp_path / 'model')
    loaded = loader(str(tmp_path / 'model'))
    monkeypatch.setattr(module, 'inference_tokenizer', lambda path: loaded)
    endpoint = {'model': 'fixture', 'max_tokens': 128, 'inference_tokenizer': str(tmp_path / 'model')}
    counted = RequestBudget(endpoint, {}).check([{'role': 'user', 'content': 'hello'}])
    assert counted['prompt_tokens_preflight'] == len(loaded.apply_chat_template([{'role': 'user', 'content': 'hello'}], tokenize=True, add_generation_prompt=True, return_dict=False))
    messages = [{'role': 'user', 'content': 'hello'}]
    assert loaded.apply_chat_template(messages, tokenize=True, add_generation_prompt=True) == fast.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)


@pytest.mark.parametrize('parameter', ['max_tokens', 'max_completion_tokens'])
@pytest.mark.parametrize('with_tokenizer', [True, False])
async def test_real_streaming_wire_budget_and_usage(compiler, tmp_path, monkeypatch, parameter, with_tokenizer):
    fake = ContentClient()
    wire = []

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        assert payload[parameter] == 1024
        assert ('max_tokens' if parameter == 'max_completion_tokens' else 'max_completion_tokens') not in payload
        assert 'tools' not in payload and 'response_format' not in payload
        message = await fake.complete({}, payload['messages'])
        base = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'local-model'}
        prompt = len(ByteChatTokenizer().apply_chat_template(payload['messages']))
        output = len(message['content'].encode()) + 7
        chunks = [
            {**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': message['content']}, 'finish_reason': None}]},
            {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': prompt, 'completion_tokens': output, 'total_tokens': prompt + output,
                       'completion_tokens_details': {'reasoning_tokens': 7}}},
        ]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text=''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n')

    original = httpx.AsyncClient

    class client(original):
        def __init__(self, **kwargs):
            kwargs.setdefault('transport', httpx.MockTransport(respond))
            super().__init__(**kwargs)

    monkeypatch.setattr('trajectory_remix.model_session.httpx.AsyncClient', client)
    app, runner = setup(tmp_path, compiler, None)
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'][0]['completion_limit_parameter'] = parameter
    if not with_tokenizer:
        config['endpoints'][0].pop('inference_tokenizer')
        config['content_first']['context_budget'] = 1
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('content_first', max_parallel=3)
    await finish(runner)
    sample = app.state.dataset['samples'][0]
    requests = sample['metadata']['generation_record']['requests']
    assert len(requests) == len(wire) == 5
    for record in requests:
        assert record['budget']['budget_enforced'] is with_tokenizer
        if with_tokenizer:
            assert record['metrics']['prompt_tokens'] == record['budget']['prompt_tokens_preflight']
        assert record['metrics']['token_source'] == 'provider'
        assert record['metrics']['reasoning_tokens'] == 7
        assert record['metrics']['total_tokens'] == record['metrics']['prompt_tokens'] + record['metrics']['completion_tokens']
    assert sample['metadata']['generation_record']['metrics']['reasoning_tokens'] == 35


async def test_delayed_parallel_observations_are_not_in_early_windows(compiler, tmp_path):
    fake = ContentClient()
    app, runner = setup(tmp_path, compiler, fake)
    flow = runner.registry.scenarios['content_first'].flow
    # Environment latency is a scenario condition, independent of model decisions.
    flow.steps['setup'].inputs['operations'] = [
        {'tool': 'hardware_load_set', 'arguments': {'file_io_ticks': 5}},
    ]
    flow.steps['parallel'].inputs['calls'] = [
        {'tool': 'calculator', 'arguments': {'expression': '2 + 2'}, 'duration-ticks': 25},
        {'tool': 'file_read', 'arguments': {'path': 'invoice.txt'}, 'duration-ticks': 50},
    ]
    runner.start('content_first', max_parallel=3)
    await finish(runner)
    sample = app.state.dataset['samples'][0]
    plan = compiler.compile(sample)
    calls = [c for c in plan['tool_calls'] if not c.get('invisible')][:2]
    assert calls[0]['request_start'] == calls[1]['request_start']
    assert calls[1]['end'] >= calls[1]['start'] + 55
    assert calls[0]['end'] == calls[0]['start']  # calculator has no latency
    from trajectory_remix.llm import selected_windows
    early = selected_windows(plan, compiler, {'visible': ['*'], 'visible_tick': calls[0]['request_start']})
    assert not any('"ok": false' in w['content'] or '"result": 4' in w['content'] for w in early)
    late = selected_windows(plan, compiler, {'visible': ['*'], 'visible_tick': max(c['end'] for c in calls)})
    assert any('false' in w['content'] for w in late)


async def test_external_repair_original_checklist_and_export(compiler, tmp_path):
    fake = ContentClient(fail='control')
    app, runner = setup(tmp_path, compiler, fake)
    runner.start('content_first', max_parallel=3)
    await runner.job
    ident = runner.status['draft_id']
    commands = app.state.commands
    reviewed = await commands.call('review_trajectory', {'sample_id': ident})
    assert reviewed['checklist']['source'] == 'generation_snapshot'
    # Repair retained draft directly with existing MCP operations, retaining failed
    # proposals and original requirements as evidence for the external reviewer.
    repaired = await commands.call('repair_trajectory', {
        'sample_id': ident, 'expected_revision': reviewed['revision'],
        'checklist_hash': reviewed['checklist']['checklist_hash'],
        'edits': [{'action': 'append', 'tick': str(compiler.compile(app.state.dataset['samples'][0])['max_tick'] + 1),
                   'event': {'op': 'tool-call', 'window': 'external-reply', 'tool': 'chat',
                             'arguments': {'content': '2 + 2 = 4. The missing invoice remains unverified.'}}}],
    })
    assert repaired['checklist'] == reviewed['checklist']
    assert app.state.dataset['samples'][0]['metadata']['external_repairs'] == 1
    reviewed = await commands.call('review_trajectory', {'sample_id': ident})
    results = {name: {'valid': True, 'feedback': []} for name in reviewed['checklist']['checks']}
    await commands.call('record_trajectory_review', {
        'sample_id': ident, 'results': results, 'reviewer': 'test external agent',
        'summary': 'Checked original arithmetic, failed file read, generated requests and delivery.',
        'expected_revision': reviewed['revision'], 'checklist_hash': reviewed['checklist']['checklist_hash'],
    })
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 2
    assert not training_eligible(sample)
    original = httpx.AsyncClient
    async with original(transport=httpx.ASGITransport(app), base_url='http://testserver', headers={'X-Remix-Token': app.state.csrf}) as http:
        assert (await http.post('/api/export', json={'indices': [0]})).text == ''
        # Existing explicit Ready approval remains necessary; generation/review
        # cannot grant it. Use the same dashboard endpoint as the export tests.
        response = await http.post('/api/samples/0/approve', json={})
        assert response.status_code == 200, response.text
        exported = await http.post('/api/export', json={'indices': [0]})
        assert exported.status_code == 200 and 'kidra-token-chunk/1' in exported.text
        analytics = (await http.get('/api/generation/analytics')).json()['modes']['content-first']
        assert analytics['repaired_samples'] == analytics['approved_samples'] == 1
    assert len(fake.messages) == 5  # review and repair used no inference


async def test_trajectory_can_exceed_every_individual_request_budget(compiler, tmp_path):
    from test_scenarios import write_scenario
    steps = {f'piece{i}': {'uses': 'llm.content', 'prompt': 'piece.md'} for i in range(12)}
    steps['assemble'] = {'uses': 'remix.assemble', 'needs': list(steps), 'inputs': {'streams': [
        {'window': f'parallel-{i}', 'content': '${steps.piece' + str(i) + '.output}', 'close': True}
        for i in range(12)
    ]}}
    root = write_scenario(tmp_path, {'generation_mode': 'content-first', 'steps': steps}, {'piece.md': 'Write one independent bounded content block.'})

    class LongContent:
        async def complete(self, endpoint, messages, tools=None):
            assert 'steps' not in messages[1]['content']
            return {'content': 'A bounded block of meaningful content. ' * 24}

    app = app_for(tmp_path, compiler, LongContent(), scenarios=root)
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'][0].update(inference_tokenizer='synthetic-byte-model', max_tokens=1024)
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner = app.state.scenario_runner
    runner.start('custom', max_parallel=4)
    await finish(runner)
    sample = app.state.dataset['samples'][0]
    plan = compiler.compile(sample)
    assert len(plan['rows']) > 8192
    requests = sample['metadata']['generation_record']['requests']
    assert len(requests) == 12
    assert all(r['metrics']['total_tokens'] + 256 <= 8192 for r in requests)
    assert all(w['close'] is not None for w in plan['windows'])


@pytest.mark.parametrize('bad', [
    {'offset': -1}, {'start': 0}, {'content': 'hidden {N_TICK:7}'},
    {'window': 'chat'}, {'op': 'append', 'source': 'assistant'},
])
def test_assembler_rejects_backdating_control_syntax_and_wrong_ownership(compiler, bad):
    from trajectory_remix.content_first import assemble_streams
    from trajectory_remix.llm import SampleBuilder, default_config
    builder = SampleBuilder(compiler, default_config())
    with pytest.raises(ValueError):
        assemble_streams(builder, {'streams': [{'window': 'a', 'content': 'text', **bad}]})


async def test_existing_scenario_can_opt_into_content_first_without_validator_endpoint(compiler, tmp_path):
    fake = ScenarioClient(tools=True)
    app = app_for(tmp_path, compiler, fake)
    runner = app.state.scenario_runner
    runner.registry.scenarios['user_problem'].flow.generation_mode = 'content-first'
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'] = [e for e in config['endpoints'] if e['id'] == 'generator']
    # Existing tools input is large; give this deliberately unmodified scenario a
    # larger, still finite request budget. The mode does not truncate its schemas.
    config['endpoints'][0].update(inference_tokenizer='synthetic-byte-model', max_tokens=1024, context_size=65536)
    config['content_first'] = {'context_budget': 65536}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('user_problem')
    await finish(runner)
    assert not any(m[0]['content'].startswith(('Validate', 'Judge')) for m in fake.messages)
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    assert len(sample['metadata']['review_checklist']['checks']) == 4
    assert sample['metadata']['concept']['success_criteria'] == 'Answer 4'


async def test_archived_generation_evidence_survives_later_attempts(compiler, tmp_path):
    app, runner = setup(tmp_path, compiler, ContentClient(fail='schema'))
    runner.start('content_first', max_parallel=3, batch_size=2)
    await runner.job
    samples = list(app.state.dataset['samples'])
    assert len(samples) == 2
    assert samples[0]['metadata']['generation_job'] != samples[1]['metadata']['generation_job']
    first = await app.state.commands.call('get_generation_record', {'sample_id': samples[0]['id'], 'request_index': 4, 'include_failures': True})
    assert 'expensive proposal, retained' in first['request']['raw_output']
    assert first['failures'][0]['inputs']
    assert not any('raw_output' in request for request in first['requests'])
    assert first['metrics']['unsuccessful_drafts'] == 1
    analytics = await app.state.commands.call('get_generation_analytics', {})
    assert analytics['modes']['content-first']['retained_candidates'] == 2
    assert analytics['modes']['content-first']['unsuccessful_drafts'] == 2


async def test_closed_window_context_is_rejected_instead_of_leaked(compiler, tmp_path):
    from test_scenarios import write_scenario
    steps = {
        'text': {'uses': 'llm.content', 'prompt': 'content.md'},
        'close': {'uses': 'remix.assemble', 'needs': ['text'], 'inputs': {'streams': [
            {'window': 'closed-evidence', 'content': '${steps.text.output}', 'close': True}]}},
        'later': {'uses': 'llm.content', 'prompt': 'content.md', 'needs': ['close'], 'visible_windows': ['closed-evidence']},
    }
    root = write_scenario(tmp_path, {'generation_mode': 'content-first', 'steps': steps}, {'content.md': 'Write bounded text.'})

    class Client:
        calls = 0
        async def complete(self, endpoint, messages, tools=None):
            self.calls += 1
            return {'content': 'Evidence before closure.'}

    fake = Client()
    app = app_for(tmp_path, compiler, fake, scenarios=root)
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation'] = {'max_attempts': 1}
    config['endpoints'][0]['inference_tokenizer'] = 'synthetic-byte-model'
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner = app.state.scenario_runner
    runner.start('custom')
    await runner.job
    assert runner.status['phase'] == 'rejected'
    assert fake.calls == 1
    assert 'not open' in json.dumps(app.state.dataset['samples'][0]['metadata']['generation_record']['failures'])


def test_operational_metrics_do_not_invalidate_review_but_original_requirements_do():
    from trajectory_remix.dataset import evaluation_source
    sample = {'metadata': {'generation_metrics': [1], 'generation_elapsed_seconds': 1,
                           'generation_record': {'requirements': 'original'}}}
    updated = copy.deepcopy(sample)
    updated['metadata'].update(generation_metrics=[2], generation_elapsed_seconds=2)
    assert evaluation_source(sample) == evaluation_source(updated)
    updated['metadata']['generation_record']['requirements'] = 'changed'
    assert evaluation_source(sample) != evaluation_source(updated)


@pytest.mark.parametrize('with_tokenizer', [True, False])
async def test_cancelled_native_stream_retains_partial_content(compiler, tmp_path, monkeypatch, with_tokenizer):
    entered = asyncio.Event()
    release = asyncio.Event()

    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunk = {'id': 'partial', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'local-model',
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '{"problem":"expensive partial'}, 'finish_reason': None}]}
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
            entered.set()
            await release.wait()

    async def respond(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Partial())

    original = httpx.AsyncClient

    class Intercepted(original):
        def __init__(self, **kwargs):
            kwargs.setdefault('transport', httpx.MockTransport(respond))
            super().__init__(**kwargs)

    monkeypatch.setattr('trajectory_remix.model_session.httpx.AsyncClient', Intercepted)
    app, runner = setup(tmp_path, compiler, None)
    if not with_tokenizer:
        config = copy.deepcopy(app.state.dataset['config'])
        config['endpoints'][0].pop('inference_tokenizer')
        app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('content_first')
    await asyncio.wait_for(entered.wait(), 5)
    # Capture a live, periodically journaled checkpoint as if its worker died.
    # SQLite backup yields a consistent independent project for restart testing.
    await asyncio.sleep(2.1)
    import sqlite3
    interrupted_path = tmp_path / 'interrupted.sqlite'
    with app.state.dataset.store.connect() as db, sqlite3.connect(interrupted_path) as backup:
        live = json.loads(db.execute('SELECT snapshot FROM generation_jobs').fetchone()[0])
        assert live['elapsed_seconds'] > 0
        assert live['content_requests'][0]['raw_output'] == '{"problem":"expensive partial'
        db.backup(backup)
    await runner.stop()
    recovered = create_app(interrupted_path)
    assert recovered.state.scenario_runner.status['phase'] == 'interrupted'
    interrupted = recovered.state.scenario_runner.snapshot['content_requests'][0]
    assert interrupted['state'] == 'interrupted' and interrupted['raw_output']
    assert recovered.state.scenario_runner.snapshot['elapsed_seconds'] > 0
    sample = app.state.dataset['samples'][0]
    raw = sample['metadata']['generation_record']['requests'][0]
    assert raw['raw_output'] == '{"problem":"expensive partial'
    assert raw['state'] == 'incomplete'
    reopened = create_app(app.state.dataset.store.path)
    retained = await reopened.state.commands.call('get_generation_record', {'sample_id': sample['id'], 'request_index': 0})
    assert retained['request']['raw_output'] == raw['raw_output']


def test_request_stats_support_inference_tokenizer_encoding_lists():
    from trajectory_remix.generation_stats import RequestStats
    stats = RequestStats('byte-model', tokenizer=ByteChatTokenizer())
    assert stats.count('café') == len('café'.encode())
    stats.estimate('abc', 'def')
    result = stats.sample(final=True)
    assert result['prompt_tokens'] == result['completion_tokens'] == 3
