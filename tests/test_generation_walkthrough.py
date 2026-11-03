"""LLM-focused request diagnostics, with normal creation and no saved report."""

import asyncio
import copy
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from trajectory_remix.generation_walkthrough import GenerationWalkthrough
from trajectory_remix.scenarios import Step
from test_content_first import ByteChatTokenizer, ContentClient, setup


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/markdown')
    assert response.headers['cache-control'] == 'no-store'
    assert 'attachment;' in response.headers['content-disposition']
    return [json.loads(body) for _, body in re.findall(r'(?m)^(`{3,})json\n([\s\S]*?)\n\1$', response.text)]


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                             headers={'X-Remix-Token': app.state.csrf})


@pytest.fixture
def traced_setup(tmp_path, compiler, monkeypatch):
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda path: ByteChatTokenizer())

    def create(fake=None):
        return setup(tmp_path, compiler, fake or ContentClient())
    return create


async def test_full_trace_exact_requests_parallelism_workspace_and_no_report_in_database(traced_setup):
    class UsageClient(ContentClient):
        async def complete(self, *args, **kwargs):
            result = await super().complete(*args, **kwargs)
            return {**result, 'reasoning_content': 'Provider supplied reasoning.',
                    '_usage': {'prompt_tokens': 100, 'completion_tokens': 40,
                               'completion_tokens_details': {'reasoning_tokens': 7}}}

    fake = UsageClient()
    app, runner = traced_setup(fake)
    # Include actual file writes, not only read-only arithmetic calls.
    complete = fake.complete

    async def with_file(*args, **kwargs):
        result = await complete(*args, **kwargs)
        if args[1][0]['content'].startswith('Create a fictional'):
            output = json.loads(result['content'])
            output['files'] = [{'path': 'trace.txt', 'content': 'workspace change'}]
            result['content'] = json.dumps(output)
        return result
    fake.complete = with_file
    async with client_for(app) as client:
        response = await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first', 'max_parallel': 3})
    trace = events(response)
    assert trace[-1]['kind'] == 'Generation finished'
    assert trace[-1]['status']['phase'] == 'complete'
    assert fake.peak == 3
    calls = [e for e in trace if e['kind'] == 'Model call finished']
    assert len(calls) == len(fake.messages) == 5
    assert sorted(json.dumps(e['messages']) for e in calls) == sorted(json.dumps(m) for m in fake.messages)
    assert all(e['response']['metrics']['completion_tokens'] == 40 for e in calls)
    assert all(e['response']['metrics']['token_source'] == 'provider' for e in calls)
    assert all(e['response']['metrics']['reasoning_tokens'] == 7 for e in calls)
    assert all(e['reasoning_status'] == 'returned by provider' for e in calls)
    assert all(e['output_checks']['original_schema'] == 'passed' for e in calls if e['original_schema'])
    structured = next(e for e in calls if e['original_schema'])
    assert structured['response_format']['type'] == 'json_schema'
    assert structured['prompt_source']
    # Overlapping calls have distinct starts paired with their completion.
    starts = {e['sequence']: e for e in trace if e['kind'] == 'Model call started'}
    assert all(starts[e['started_event']]['path'] == e['path'] for e in calls)
    assert any(sum(start < e['sequence'] < finish for start, finish in
                   [(c['started_event'], c['sequence']) for c in calls]) >= 2 for e in trace)
    assert not any(e['kind'] == 'Compiler result' for e in trace)
    for entry in trace:
        assert not {'workspace', 'workspace_before', 'workspace_changes', 'sandbox_timeline',
                    'parsed_output', 'output', 'inputs', 'history'} & entry.keys()
        if entry['kind'] == 'Model call finished':
            assert 'response_format' not in entry['response']
            assert 'reasoning' not in entry  # Retained once in response.
        if entry['kind'] == 'Step finished':
            assert set(entry['record']) <= {'state', 'error', 'detail'}
    assert all(e['elapsed_seconds'] >= 0 for e in trace if e['kind'].endswith(' finished'))
    assert 'Attempts: 1. Model calls: 5.' in response.text
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['generation_phase'] == 'complete'
    assert sample['metadata']['validation_level'] == 0
    with app.state.dataset.store.connect() as db:
        dump = '\n'.join(db.iterdump())
    for report_only in ('workspace_before', 'sandbox_timeline', 'reasoning_status', '# Scenario generation walkthrough'):
        assert report_only not in dump
    assert runner.walkthrough is None
    assert not hasattr(runner.builder, 'plan_observer')


@pytest.mark.parametrize('failure', ['schema', 'length'])
async def test_failed_generation_downloads_partial_responses_and_releases_collector(traced_setup, failure):
    app, runner = traced_setup(ContentClient(fail=failure))
    async with client_for(app) as client:
        response = await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'})
        assert 'walkthrough' not in (await client.get('/api/scenarios/job')).json()
    trace = events(response)
    failed = next(e for e in trace if e['kind'] == 'Model call finished' and 'error' in e)
    assert failed['response']['raw_output']
    assert failed['response']['state'] == 'incomplete'
    assert failed['reasoning_status'] == 'unavailable'
    assert trace[-1]['status']['phase'] == 'rejected'
    assert runner.walkthrough is None


async def test_preflight_failure_retains_exact_prompt_without_calling_provider(traced_setup):
    fake = ContentClient()
    app, runner = traced_setup(fake)
    config = copy.deepcopy(app.state.dataset['config'])
    config['content_first'] = {'context_budget': 1024, 'reserve_tokens': 256}
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    async with client_for(app) as client:
        trace = events(await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
    call = next(e for e in trace if e['kind'] == 'Model call finished')
    assert call['messages'] and call['original_schema']
    assert not call['provider_request_sent']
    assert 'budget exceeded' in call['error']['detail']
    assert not fake.messages
    assert runner.walkthrough is None


async def test_cancel_returns_partial_download_and_busy_request_cannot_attach_trace(traced_setup):
    fake = ContentClient(hold=True)
    app, runner = traced_setup(fake)
    async with client_for(app) as client:
        pending = asyncio.create_task(client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
        await asyncio.wait_for(fake.entered.wait(), 5)
        collector = runner.walkthrough
        assert (await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'})).status_code == 409
        assert runner.walkthrough is collector
        await runner.stop()
        trace = events(await asyncio.wait_for(pending, 5))
    assert trace[-1]['status']['phase'] == 'cancelled'
    assert any(e.get('error', {}).get('type') == 'CancelledError' for e in trace)
    assert runner.walkthrough is None
    assert not hasattr(runner.builder, 'plan_observer')


async def test_skips_retries_and_fallbacks_are_visible(traced_setup):
    app, runner = traced_setup()
    steps = runner.registry.scenarios['content_first'].flow.steps
    steps['skip_trace'] = Step(uses='remix.reject', when=False)
    steps['fallback_trace'] = Step(uses='remix.tools', inputs={'calls': 'invalid'}, retries=1,
                                   on_failure={'action': 'fallback', 'value': {'fallback': True}})
    async with client_for(app) as client:
        trace = events(await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
    skipped = next(e for e in trace if e['kind'] == 'Step finished' and e['path'] == 'skip_trace')
    assert skipped['record']['state'] == 'skipped'
    fallback = next(e for e in trace if e['kind'] == 'Step finished' and e['path'] == 'fallback_trace')
    assert fallback['record']['state'] == 'fallback'
    assert len([e for e in trace if e['kind'] == 'Operation finished' and e['path'] == 'fallback_trace']) == 2


@pytest.mark.parametrize('secret', ['trace-test-secret', 'trace-"quoted"-key\\with\nnewline'])
async def test_auth_validation_and_redaction(traced_setup, secret):
    app, runner = traced_setup()
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'][0]['api_key'] = secret
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.registry.scenarios['content_first'].prompts['arithmetic.md'] += '\n' + secret + '\n```json\n{}\n```'
    async with client_for(app) as client:
        denied = await client.post('/api/scenarios/walkthrough', json={}, headers={'X-Remix-Token': 'bad'})
        assert denied.status_code == 403
        assert (await client.post('/api/scenarios/walkthrough', json={'batch_size': 0})).status_code == 422
        assert runner.walkthrough is None
        response = await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'})
    assert secret not in response.text
    assert json.dumps(secret)[1:-1] not in response.text
    assert '[redacted]' in response.text
    assert events(response)[-1]['status']['phase'] == 'complete'


async def test_provider_error_returns_api_paused_report(traced_setup):
    class FailingClient(ContentClient):
        async def complete(self, *args, **kwargs):
            raise httpx.ConnectError('Provider is offline')

    app, runner = traced_setup(FailingClient())
    async with client_for(app) as client:
        trace = events(await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
    assert trace[-1]['status']['phase'] == 'api_paused'
    call = next(e for e in trace if e['kind'] == 'Model call finished')
    assert call['error']['type'] == 'ProviderFailure'
    assert call['response']['state'] == 'incomplete'
    assert runner.walkthrough is None


async def test_batch_download_contains_every_attempt(traced_setup):
    app, runner = traced_setup()
    async with client_for(app) as client:
        trace = events(await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first', 'batch_size': 2}))
    attempts = [e for e in trace if e['kind'] == 'Generation attempt']
    assert len(attempts) == 2
    assert len({e['job_id'] for e in attempts}) == 2
    assert trace[-1]['status']['batch']['completed'] == 2
    assert len(app.state.dataset['samples']) == 2
    assert runner.walkthrough is None


async def test_disconnected_request_discards_trace_and_leaves_normal_generation_running(traced_setup):
    fake = ContentClient(hold=True)
    app, runner = traced_setup(fake)
    async with client_for(app) as client:
        pending = asyncio.create_task(client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
        await asyncio.wait_for(fake.entered.wait(), 5)
        collector = runner.walkthrough
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert runner.status['running']
        assert runner.walkthrough is None
        assert collector.closed and not collector.events
        fake.release.set()
        await asyncio.wait_for(runner.job, 5)
    assert runner.status['phase'] == 'complete'
    assert not collector.events  # In-flight scopes do not recreate the discarded report.


async def test_credentials_cannot_be_recovered_from_nested_inputs_or_responses(traced_setup):
    secret = 'credential-"quote"-\\slash-\nnewline-π'

    class EchoClient(ContentClient):
        async def complete(self, *args, **kwargs):
            result = await super().complete(*args, **kwargs)
            if args[1][0]['content'].startswith('Create a fictional'):
                output = json.loads(result['content'])
                output['problem'] = secret
                output['situation'] = json.dumps({'nested': secret})
                result['content'] = json.dumps(output)
            return result

    app, runner = traced_setup(EchoClient())
    runner.registry.scenarios['content_first'].flow.steps['arithmetic'].inputs = {
        'credential': secret, 'nested': json.dumps({'credential': secret}),
    }
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'][0]['api_key'] = secret
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    async with client_for(app) as client:
        trace = events(await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
    calls = [e for e in trace if e['kind'] == 'Model call finished']
    idea = next(e for e in calls if e['prompt_source'] == 'brief.md')
    output = json.loads(idea['response']['raw_output'])
    assert output['problem'] == '[redacted]'
    assert json.loads(output['situation'])['nested'] == '[redacted]'
    downstream = [json.loads(e['messages'][1]['content']) for e in calls if e is not idea]
    inputs = next(data for data in downstream if 'credential' in data)
    assert inputs['credential'] == '[redacted]'
    assert json.loads(inputs['nested'])['credential'] == '[redacted]'

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                check(key)
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str):
            assert secret not in value
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                return
            check(decoded)
    check(trace)


@pytest.mark.parametrize('paused', [False, True])
async def test_http_disconnect_detaches_collector_without_cancelling_worker(traced_setup, paused):
    fake = ContentClient(hold=True)
    app, runner = traced_setup(fake)
    incoming = asyncio.Queue()
    await incoming.put({'type': 'http.request', 'body': b'{"scenario":"content_first"}', 'more_body': False})
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'POST', 'scheme': 'http', 'path': '/api/scenarios/walkthrough',
             'raw_path': b'/api/scenarios/walkthrough', 'query_string': b'',
             'root_path': '', 'server': ('testserver', 80), 'client': ('127.0.0.1', 12345),
             'headers': [(b'host', b'testserver'), (b'content-type', b'application/json'),
                         (b'x-remix-token', app.state.csrf.encode())]}

    async def send(message):
        pass

    handler = asyncio.create_task(app(scope, incoming.get, send))
    try:
        await asyncio.wait_for(fake.entered.wait(), 5)
        collector = runner.walkthrough
        if paused:
            runner.pause()
            fake.release.set()
            async with asyncio.timeout(5):
                while not runner.status['paused'] or runner.status['active']:
                    await asyncio.sleep(0)
        await incoming.put({'type': 'http.disconnect'})
        await asyncio.wait_for(handler, 5)
        assert runner.status['running'] and not runner.job.done()
        assert runner.walkthrough is None
        assert collector.closed and not collector.events
        fake.release.set()
        if paused:
            runner.resume()
        await asyncio.wait_for(runner.job, 5)
        assert runner.status['phase'] == 'complete'
        assert not collector.events
    finally:
        handler.cancel()
        await asyncio.gather(handler, return_exceptions=True)
        await runner.stop()


def test_collector_never_copies_workspace_or_non_model_payloads():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise AssertionError('Trajectory or operation payload was copied')

    runner = SimpleNamespace(state={'config': {'endpoints': []}}, builder=Uncopyable())
    collector = GenerationWalkthrough(runner)
    with collector.scope('Operation', 'assemble', uses='remix.assemble', inputs=Uncopyable()) as event:
        event['output'] = Uncopyable()
    with collector.scope('Step', 'work', definition={
        'uses': 'flow.repeat', 'needs': [], 'retries': 0, 'on_failure': {'action': 'reject'},
    }) as event:
        event['record'] = {'state': 'complete', 'output': Uncopyable(), 'inputs': Uncopyable()}
        event['output'] = Uncopyable()
    collector.finished({'phase': 'complete', 'metrics': Uncopyable()})
    assert collector.events[-1]['status'] == {'phase': 'complete'}
    assert collector.events[-2]['record'] == {'state': 'complete'}
    assert collector.events[1]['kind'] == 'Operation finished'
    assert 'output' not in collector.events[1]


@pytest.mark.parametrize('reopen', [False, True])
async def test_walkthrough_honors_prompt_only_saved_via_api(traced_setup, reopen):
    from trajectory_remix.server import create_app

    fake = ContentClient()
    app, runner = traced_setup(fake)
    config = copy.deepcopy(app.state.dataset['config'])
    config['endpoints'][0]['structured_output'] = 'prompted'
    async with client_for(app) as client:
        saved = await client.put('/api/config', json={'config': config})
        assert saved.status_code == 200, saved.text
    if reopen:
        app = create_app(app.state.dataset.store.path, client=fake,
                         scenarios_dir=runner.registry.root)
    async with client_for(app) as client:
        trace = events(await client.post('/api/scenarios/walkthrough', json={'scenario': 'content_first'}))
    calls = [e for e in trace if e['kind'] == 'Model call finished' and e['original_schema']]
    assert calls
    assert all(e['endpoint']['structured_output'] == 'prompted' for e in calls)
    assert all(e['output_mode'] == 'prompted' and e['response_format'] is None for e in calls)
    assert trace[-1]['status']['phase'] == 'complete'
