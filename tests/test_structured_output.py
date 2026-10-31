"""Native server contracts, exact schema preservation and recoverable API errors."""
import copy
import json

import httpx
import jsonschema
import pytest

from trajectory_remix.content_first import RequestBudget
from trajectory_remix.llm import LLMClient, default_config, validate_config
from trajectory_remix.structured_output import decoding_schema, native_output, response_format
from test_content_first import ByteChatTokenizer, ContentClient, setup
from test_scenarios import app_for, finish, write_scenario


def streaming_response(content, *, finish='stop', reasoning=None, usage=True):
    base = {'id': 'schema-test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'local-model'}
    delta = {'role': 'assistant', 'content': content}
    if reasoning:
        delta['reasoning_content'] = reasoning
    chunks = [
        {**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
        {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
    ]
    if usage:
        chunks[-1]['usage'] = {'prompt_tokens': 50, 'completion_tokens': 25, 'total_tokens': 75,
                              'completion_tokens_details': {'reasoning_tokens': 7}}
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          text=''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n')


def wire_transport(monkeypatch, handler):
    original = httpx.AsyncClient

    class Client(original):
        def __init__(self, **kwargs):
            kwargs.setdefault('transport', httpx.MockTransport(handler))
            super().__init__(**kwargs)

    monkeypatch.setattr('trajectory_remix.model_session.httpx.AsyncClient', Client)


@pytest.mark.parametrize('mode', ['native', 'prompted'])
def test_endpoint_output_mode_configuration(mode):
    config = default_config()
    assert all(ep['structured_output'] == 'native' for ep in config['endpoints'])
    config['endpoints'][0]['structured_output'] = mode
    validate_config(config)
    del config['endpoints'][0]['structured_output']
    validate_config(config)  # Historical configurations remain readable.


@pytest.mark.parametrize('invalid', [None, False, '', 'json_object', {}, []])
def test_invalid_endpoint_output_mode(invalid):
    config = default_config()
    config['endpoints'][0]['structured_output'] = invalid
    with pytest.raises(ValueError, match='structured_output'):
        validate_config(config)


def test_native_schema_preserves_open_arguments_optional_fields_and_constraints():
    schema = {'type': 'object', 'properties': {
        'calls': {'type': 'array', 'maxItems': 8, 'items': {'type': 'object', 'properties': {
            'tool': {'type': 'string', 'enum': ['custom']}, 'arguments': {'type': 'object'},
            'outcome': {'type': 'object'},
        }, 'required': ['tool', 'arguments'], 'additionalProperties': False}},
        'reasoning': {'type': 'string', 'minLength': 1},
    }, 'required': ['calls', 'reasoning'], 'additionalProperties': False}
    before = copy.deepcopy(schema)
    output = native_output({}, schema, 'custom.output')
    formatting = response_format(output)
    assert formatting['json_schema'] == {'name': 'custom_output', 'schema': schema, 'strict': True}
    assert output.json_schema == before and schema == before
    jsonschema.validate({'reasoning': 'Read the provided record.', 'calls': [
        {'tool': 'custom', 'arguments': {'arbitrary_custom_field': 3}},
    ]}, output.json_schema)
    output.json_schema['properties']['reasoning']['minLength'] = 100
    assert schema == before
    assert response_format(native_output({}, None)) is None
    assert response_format(native_output({'structured_output': 'prompted'}, schema)) is None


def test_schema_overhead_is_reserved_before_inference(monkeypatch):
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: ByteChatTokenizer())
    endpoint = {'inference_tokenizer': 'bytes', 'max_tokens': 20}
    messages = [{'role': 'user', 'content': 'Return the answer.'}]
    formatting = response_format(native_output({}, {'type': 'object', 'properties': {'answer': {'type': 'string'}}}))
    budget = RequestBudget(endpoint, {'context_budget': 8192, 'reserve_tokens': 16})
    baseline = budget.check(messages)
    counted = budget.check(messages, response_format=formatting)
    assert counted['prompt_tokens_preflight'] == baseline['prompt_tokens_preflight']
    assert counted['structured_output_tokens_reserved'] > 0
    assert counted['request_input_tokens_preflight'] == counted['prompt_tokens_preflight'] + counted['structured_output_tokens_reserved']
    budget.context = baseline['prompt_tokens_preflight'] + 20 + 16
    budget.check(messages)  # Plain messages fit exactly; native allowance does not.
    with pytest.raises(ValueError, match='schema allowance'):
        budget.check(messages, response_format=formatting)
    budget.context = 8192
    with pytest.raises(ValueError, match='input cap'):
        budget.check(messages, baseline['prompt_tokens_preflight'], response_format=formatting)


@pytest.mark.parametrize('status', [400, 422])
async def test_schema_rejection_pauses_once_and_resume_reuses_completed_steps(compiler, tmp_path, monkeypatch, status):
    fake = ContentClient()
    wire = []

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        if payload.get('response_format', {}).get('json_schema', {}).get('name') == 'Decisions':
            return httpx.Response(status, json={'error': {'message': 'SECRET native schema unsupported'}})
        message = await fake.complete({}, payload['messages'])
        return streaming_response(message['content'])

    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: ByteChatTokenizer())
    wire_transport(monkeypatch, respond)
    app, runner = setup(tmp_path, compiler, None)
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation']['api_retries'] = 2
    config['single_generation']['max_attempts'] = 3
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('content_first')
    await runner.job
    assert runner.status['phase'] == 'api_paused'
    assert runner.status['batch']['attempts'] == 1
    assert 'Prompt-only JSON' in runner.status['last_error']
    assert 'SECRET' not in runner.status['last_error']
    assert sum(p.get('response_format', {}).get('json_schema', {}).get('name') == 'Decisions' for p in wire) == 1
    sample = copy.deepcopy(app.state.dataset['samples'][0])
    assert sample['metadata']['validation_level'] == 0
    assert sample['metadata']['generation_phase'] == 'planning'
    assert runner.snapshot['steps']['idea']['state'] == 'complete'
    original_idea_requests = sum(p['messages'][0]['content'].startswith('Create a fictional') for p in wire)
    config['endpoints'][0]['structured_output'] = 'prompted'
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.resume_saved(sample['id'])
    await finish(runner)
    assert sum(p['messages'][0]['content'].startswith('Create a fictional') for p in wire) == original_idea_requests == 1
    assert len(app.state.dataset['samples']) == 1
    assert app.state.dataset['samples'][0]['uuid'] == sample['uuid']
    record = app.state.dataset['samples'][0]['metadata']['generation_record']
    assert record['requests'][0]['output_mode'] == 'native'
    assert record['requests'][-1]['output_mode'] == 'prompted'


@pytest.mark.parametrize('mode', ['content-first', 'existing'])
async def test_native_wire_and_local_validation_retain_nonconforming_response(compiler, tmp_path, monkeypatch, mode):
    schema = {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1},
                                             'optional_arguments': {'type': 'object'}},
              'required': ['text'], 'additionalProperties': False}
    root = write_scenario(tmp_path, {'generation_mode': mode, 'schemas': {'Message': schema},
        'steps': {'message': {'uses': 'llm.content', 'prompt': 'prompt.md', 'output_schema': 'Message'}}})
    wire = []

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        assert payload['response_format'] == {'type': 'json_schema', 'json_schema': {
            'name': 'Message', 'schema': schema, 'strict': True}}
        assert payload['messages'][0]['role'] == 'system'
        return streaming_response('{"wrong":"retained expensive response"}', reasoning='Private provider reasoning.')

    wire_transport(monkeypatch, respond)
    app = app_for(tmp_path, compiler, None, scenarios=root)
    runner = app.state.scenario_runner
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation']['max_attempts'] = 1
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('custom')
    await runner.job
    assert len(wire) == 1
    assert runner.status['phase'] == 'rejected'
    assert runner.snapshot['steps']['message']['error'] == 'ValidationError'
    request = runner.snapshot['content_requests'][0]
    assert request['raw_output'] == '{"wrong":"retained expensive response"}'
    assert request['reasoning'] == 'Private provider reasoning.'
    assert request['output_mode'] == 'native'
    assert request['metrics']['reasoning_tokens'] == 7
    assert request['metrics']['completion_tokens'] == 25


async def test_injected_http_client_receives_native_format(compiler, tmp_path):
    schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}
    root = write_scenario(tmp_path, {'generation_mode': 'content-first', 'schemas': {'Message': schema},
        'steps': {
            'message': {'uses': 'llm.content', 'prompt': 'prompt.md', 'output_schema': 'Message'},
            'draft': {'uses': 'remix.draft', 'needs': ['message'], 'inputs': {'response': '${steps.message.output.text}'}},
        }})
    wire = []

    def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        assert payload['response_format']['json_schema']['schema'] == schema
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{"text":"A generated answer."}'}}]})

    app = app_for(tmp_path, compiler, LLMClient(httpx.MockTransport(respond)), scenarios=root)
    runner = app.state.scenario_runner
    runner.start('custom')
    await finish(runner)
    assert len(wire) == 1
    assert app.state.dataset['samples'][0]['metadata']['validation_level'] == 0


async def test_native_schema_allowance_blocks_request_before_network(compiler, tmp_path, monkeypatch):
    monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: ByteChatTokenizer())
    schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}
    root = write_scenario(tmp_path, {'generation_mode': 'content-first', 'schemas': {'Message': schema},
        'steps': {'message': {'uses': 'llm.content', 'prompt': 'prompt.md', 'output_schema': 'Message'}}})
    fake = ContentClient()  # Must never be called for this prompt.
    app = app_for(tmp_path, compiler, fake, scenarios=root)
    runner = app.state.scenario_runner
    runner.start('custom', _schedule=False)
    step = runner.scenario.flow.steps['message']
    instructions, data = runner.render_prompt(step, {}, runner.context)
    messages = [{'role': 'system', 'content': instructions}, {'role': 'user', 'content': data}]
    message_tokens = len(ByteChatTokenizer().apply_chat_template(messages))
    runner.config['endpoints'][0].update(inference_tokenizer='bytes', max_tokens=20)
    runner.config['content_first'].update(context_budget=message_tokens + 20 + 16, reserve_tokens=16)
    await runner.run_attempt()
    assert runner.status['phase'] == 'rejected'
    assert runner.status['model_requests'] == 0
    assert not fake.messages
    assert 'schema allowance' in runner.snapshot['steps']['message']['detail']
    assert app.state.dataset['samples'][0]['metadata']['validation_level'] == 0


async def test_injected_http_schema_rejection_is_recoverable_not_a_rejection(compiler, tmp_path):
    schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}
    root = write_scenario(tmp_path, {'generation_mode': 'content-first', 'schemas': {'Message': schema},
        'steps': {'message': {'uses': 'llm.content', 'prompt': 'prompt.md', 'output_schema': 'Message'}}})
    wire = []

    def respond(request):
        wire.append(json.loads(request.content))
        return httpx.Response(400, text='SECRET unsupported response_format')

    app = app_for(tmp_path, compiler, LLMClient(httpx.MockTransport(respond)), scenarios=root)
    runner = app.state.scenario_runner
    runner.start('custom')
    await runner.job
    assert runner.status['phase'] == 'api_paused'
    assert len(wire) == 1
    assert runner.status['batch']['attempts'] == 1
    assert 'Prompt-only JSON' in runner.status['last_error']
    assert 'SECRET' not in runner.status['last_error']
    assert app.state.dataset['samples'][0]['metadata']['generation_phase'] == 'planning'


def test_decoding_projection_keeps_original_uniqueness_and_literal_keyword_data():
    schema = {'type': 'object', 'properties': {
        'uniqueItems': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True, 'maxItems': 4},
        'literal': {'const': {'uniqueItems': True}, 'default': {'uniqueItems': True},
                    'examples': [{'uniqueItems': True}], 'enum': [{'uniqueItems': True}]},
    }, 'required': ['uniqueItems'], 'additionalProperties': False,
        '$defs': {'uniqueItems': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'integer'}}},
        'allOf': [{'properties': {'other': {'$ref': '#/$defs/uniqueItems'}}}]}
    before = copy.deepcopy(schema)
    constraints = []
    projected = decoding_schema(schema, constraints)
    assert schema == before
    assert projected['properties']['uniqueItems'] == {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4}
    assert projected['properties']['literal'] == schema['properties']['literal']
    assert projected['$defs']['uniqueItems'] == {'type': 'array', 'items': {'type': 'integer'}}
    assert projected['allOf'] == schema['allOf']
    assert constraints == [
        {'keyword': 'uniqueItems', 'schema_path': '/properties/uniqueItems/uniqueItems'},
        {'keyword': 'uniqueItems', 'schema_path': '/$defs/uniqueItems/uniqueItems'},
    ]
    duplicate = {'uniqueItems': ['same', 'same']}
    jsonschema.validate(duplicate, projected)
    with pytest.raises(jsonschema.ValidationError, match='non-unique'):
        jsonschema.validate(duplicate, schema)


@pytest.mark.parametrize('keyword', ['items', 'prefixItems'])
def test_decoding_projection_handles_nested_array_schemas_and_escaped_paths(keyword):
    schema = {'type': 'array', keyword: [{'type': 'array', 'uniqueItems': True,
                                       'items': {'type': 'string'}}], 'uniqueItems': False}
    constraints = []
    projected = decoding_schema(schema, constraints)
    assert 'uniqueItems' not in projected and 'uniqueItems' not in projected[keyword][0]
    assert constraints == [{'keyword': 'uniqueItems', 'schema_path': f'/{keyword}/0/uniqueItems'}]
    original = {'properties': {'a~/b': {'type': 'array', 'uniqueItems': True}},
                'additionalProperties': False}
    constraints = []
    decoding_schema(original, constraints)
    assert constraints[0]['schema_path'] == '/properties/a~0~1b/uniqueItems'
    assert native_output({'structured_output': 'prompted'}, original, local_constraints=constraints) is None
    assert len(constraints) == 1  # Prompt-only mode never claims a native projection.


@pytest.mark.parametrize('mode,with_tokenizer', [('content-first', True), ('content-first', False), ('existing', False)])
@pytest.mark.parametrize('duplicates', [False, True])
async def test_unique_items_compatibility_wire_and_original_schema_checks(
    compiler, tmp_path, monkeypatch, mode, with_tokenizer, duplicates,
):
    schema = {'type': 'object', 'properties': {
        'values': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True, 'maxItems': 4},
        'text': {'type': 'string', 'minLength': 1},
    }, 'required': ['values', 'text'], 'additionalProperties': False}
    root = write_scenario(tmp_path, {'generation_mode': mode, 'schemas': {'Selection': schema}, 'steps': {
        'pick': {'uses': 'llm.content', 'prompt': 'prompt.md', 'output_schema': 'Selection'},
        'draft': {'uses': 'remix.draft', 'needs': ['pick'], 'inputs': {'response': '${steps.pick.output.text}'}},
    }})
    wire = []
    result = {'values': ['first', 'first' if duplicates else 'second'], 'text': 'Selected the stated values.'}

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        formatting = payload['response_format']
        assert formatting['type'] == 'json_schema' and formatting['json_schema']['strict'] is True
        actual = formatting['json_schema']['schema']
        if 'uniqueItems' in actual['properties']['values']:
            return httpx.Response(400, json={'error': {'message': 'unsupported JSON Schema keyword: uniqueItems'}})
        assert actual['properties']['values']['maxItems'] == 4
        assert '"uniqueItems":true' in payload['messages'][0]['content']
        jsonschema.validate(result, actual)  # Even duplicates satisfy the decoding subset.
        return streaming_response(json.dumps(result))

    wire_transport(monkeypatch, respond)
    app = app_for(tmp_path, compiler, None, scenarios=root)
    runner = app.state.scenario_runner
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation']['max_attempts'] = 1
    if with_tokenizer:
        monkeypatch.setattr('trajectory_remix.content_first.inference_tokenizer', lambda _: ByteChatTokenizer())
        config['endpoints'][0].update(inference_tokenizer='bytes', max_tokens=1024)
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('custom')
    await runner.job
    assert len(wire) == 1  # No rejected probe, grammar downgrade or extra inference.
    assert runner.status['phase'] == ('rejected' if duplicates else 'complete')
    assert runner.scenario.flow.schemas['Selection'] == schema
    proposal = runner.snapshot['content_requests'][0]
    assert proposal['output_mode'] == 'native'
    assert proposal['local_schema_constraints'] == [{'keyword': 'uniqueItems', 'schema_path': '/properties/values/uniqueItems'}]
    assert proposal['metrics']['local_schema_keywords'] == ['uniqueItems']
    assert proposal['metrics']['budget_enforced'] is with_tokenizer
    assert any('uniqueItems is enforced locally' in e['message'] for e in runner.logs)
    if duplicates:
        assert runner.snapshot['steps']['pick']['error'] == 'ValidationError'
        assert json.loads(proposal['raw_output']) == result  # Never silently deduplicate model output.
        if mode == 'content-first':
            sample = app.state.dataset['samples'][0]
            assert sample['metadata']['validation_level'] == 0
            assert sample['metadata']['generation_phase'] == 'planning'
            assert sample['metadata']['generation_record']['failures']
    else:
        assert runner.snapshot['steps']['pick']['output'] == result
        assert app.state.dataset['samples'][0]['metadata']['validation_level'] == 0


async def test_bundled_idle_flow_uses_compatible_native_schema(compiler, tmp_path, monkeypatch):
    import asyncio
    from test_idle_scenario import IdleClient

    fake = IdleClient('parallel')
    wire = []

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        schema = payload['response_format']['json_schema']['schema']
        if 'release_windows' in schema['properties']:
            assert 'uniqueItems' not in schema['properties']['release_windows']
            assert 'allOf' not in schema  # Conditional-only wrapper is eliminated.
        message = await fake.complete({'id': 'generator'}, payload['messages'])
        jsonschema.validate(json.loads(message['content']), schema)
        return streaming_response(message['content'])

    wire_transport(monkeypatch, respond)
    app = app_for(tmp_path, compiler, None, scenarios=None, legacy_reviews=False)
    runner = app.state.scenario_runner
    runner.start('idle_scenario')
    await asyncio.wait_for(runner.job, 15)
    assert runner.status['phase'] == 'complete', list(runner.logs)
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    assert len(wire) == len(fake.messages) == 5
    assert all(p['output_mode'] == 'native' for p in sample['metadata']['generation_record']['requests'])
    assert runner.scenario.flow.schemas['Action']['properties']['release_windows']['uniqueItems'] is True


async def test_shared_review_omits_unique_items_only_on_wire(studio, monkeypatch):
    from test_standalone_validation import attach
    from test_scenarios import review_output

    app, _ = studio
    wire = []

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        schema = payload['response_format']['json_schema']['schema']
        assert 'uniqueItems' not in schema['properties']['context_requests']
        assert 'allOf' not in schema['$defs']['item']
        result = review_output(payload['messages'])
        jsonschema.validate(result, schema)
        return streaming_response(json.dumps(result))

    wire_transport(monkeypatch, respond)
    validator = attach(app, None)
    result = await validator.validate('test', record=False)
    assert result['valid'] and not result['recorded']
    assert len(wire) == 4
    for proposal in validator.snapshot['content_requests']:
        constraints = proposal['local_schema_constraints']
        assert {'keyword': 'uniqueItems', 'schema_path': '/properties/context_requests/uniqueItems'} in constraints
        assert {c['keyword'] for c in constraints} == {'uniqueItems', 'if', 'then', 'else'}
        assert proposal['metrics']['local_schema_keywords'] == ['else', 'if', 'then', 'uniqueItems']


def test_conditional_projection_preserves_property_names_and_literal_data():
    literal = {'if': {'then': 1}, 'else': 'data'}
    schema = {'type': 'object', 'properties': {
        'if': {'type': 'boolean'}, 'then': {'type': 'string'},
        'else': {'enum': [literal], 'default': literal, 'examples': [literal]},
    }, 'required': ['if'], 'additionalProperties': False, 'allOf': [{
        'if': {'properties': {'if': {'const': True}}},
        'then': {'required': ['then']}, 'else': {'required': ['else']},
    }]}
    before = copy.deepcopy(schema)
    constraints = []
    projected = decoding_schema(schema, constraints)
    assert schema == before
    assert projected['properties'] == schema['properties']
    assert 'allOf' not in projected
    assert constraints == [{'keyword': k, 'schema_path': f'/allOf/0/{k}'} for k in ('if','then','else')]
    for value in [{'if': True, 'then': 'required'}, {'if': False, 'else': literal}]:
        jsonschema.validate(value, schema)
        jsonschema.validate(value, projected)
    jsonschema.validate({'if': True}, projected)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'if': True}, schema)


@pytest.mark.parametrize('schema,values', [
    ({'not': {'type': 'array', 'uniqueItems': True}}, [[1,1], 'text', {}, [1,2]]),
    ({'$defs': {'distinct': {'type': 'array', 'uniqueItems': True}},
      'not': {'$ref': '#/$defs/distinct'}}, [[1,1], 'text', {}, [1,2]]),
    ({'$defs': {'a b': {'type': 'array', 'uniqueItems': True}},
      'not': {'$ref': '#/$defs/a%20b'}}, [[1,1], 'text', {}, [1,2]]),
    ({'$defs': {'distinct': {'$anchor': 'distinct', 'type': 'array', 'uniqueItems': True}},
      'not': {'$ref': '#distinct'}}, [[1,1], 'text', {}, [1,2]]),
    ({'oneOf': [{'type': 'array', 'uniqueItems': True}, {'const': ['x','x']}]}, [['a','b'], ['x','x'], [], 1]),
    ({'anyOf': [{'type': 'array'}, {'type': 'string'}],
      'oneOf': [{'type': 'array', 'uniqueItems': True}, {'const': ['x','x']}]}, [['a'], ['x','x'], 'text']),
    ({'$defs': {'distinct': {'type': 'array', 'uniqueItems': True}},
      'oneOf': [{'$ref': '#/$defs/distinct'}, {'const': ['x','x']}]}, [['a'], ['x','x'], []]),
    ({'type': 'array', 'contains': {'type': 'array', 'uniqueItems': True}, 'minContains': 0, 'maxContains': 1},
     [[[1],[2,2]], [[1],[2]], [[1,1]], []]),
    ({'type': 'object', 'properties': {'flag': {'type': 'boolean'}}, 'required': ['flag'],
      'if': {'properties': {'flag': {'const': True}}}, 'then': {'properties': {'extra': {'type': 'string'}}},
      'unevaluatedProperties': False}, [{'flag': True, 'extra': 'valid'}, {'flag': False}, {'flag': True, 'extra': 1}]),
    ({'type': 'array', 'if': {'contains': {'const': 1}}, 'then': {'items': {'type': 'integer'}},
      'unevaluatedItems': False}, [[1,2], [], [1,'invalid']]),
    ({'type': 'object', 'anyOf': [{}, {'properties': {'annotated': {}}}], 'unevaluatedProperties': False},
     [{'annotated': 1}, {}, {'other': 1}]),
])
def test_projection_never_excludes_original_valid_values_under_semantic_operators(schema, values):
    original = copy.deepcopy(schema)
    projected = decoding_schema(schema)
    assert schema == original
    jsonschema.Draft202012Validator.check_schema(projected)
    authoritative = jsonschema.Draft202012Validator(schema)
    decoder = jsonschema.Draft202012Validator(projected)
    valid = [v for v in values if authoritative.is_valid(v)]
    assert valid
    assert all(decoder.is_valid(value) for value in valid)


@pytest.mark.parametrize('mode', ['content-first', 'existing'])
@pytest.mark.parametrize('finished,calls,valid', [(True, [], True), (False, ['work'], True),
                                                 (True, ['work'], False), (False, [], False)])
async def test_native_conditional_constraints_are_checked_against_original_schema(
    compiler, tmp_path, monkeypatch, mode, finished, calls, valid,
):
    schema = {'type': 'object', 'properties': {
        'finished': {'type': 'boolean'}, 'calls': {'type': 'array', 'items': {'type': 'string'}},
        'text': {'type': 'string'},
    }, 'required': ['finished','calls','text'], 'additionalProperties': False,
        'allOf': [{'if': {'properties': {'finished': {'const': True}}},
                   'then': {'properties': {'calls': {'maxItems': 0}}},
                   'else': {'properties': {'calls': {'minItems': 1}}}}]}
    root = write_scenario(tmp_path, {'generation_mode': mode, 'schemas': {'Action': schema}, 'steps': {
        'decide': {'uses': 'llm.content', 'prompt': 'prompt.md', 'output_schema': 'Action'},
        'draft': {'uses': 'remix.draft', 'needs': ['decide'], 'inputs': {'response': '${steps.decide.output.text}'}},
    }})
    result = {'finished': finished, 'calls': calls, 'text': 'A generated decision.'}
    wire = []

    async def respond(request):
        payload = json.loads(request.content)
        wire.append(payload)
        actual = payload['response_format']['json_schema']['schema']
        if 'allOf' in actual:
            return httpx.Response(400, json={'error': {'message': 'unsupported JSON Schema keyword: if'}})
        assert actual['properties'] == schema['properties']
        assert payload['response_format']['json_schema']['strict'] is True
        assert '"if"' in payload['messages'][0]['content']
        jsonschema.validate(result, actual)
        return streaming_response(json.dumps(result))

    wire_transport(monkeypatch, respond)
    app = app_for(tmp_path, compiler, None, scenarios=root)
    runner = app.state.scenario_runner
    config = copy.deepcopy(app.state.dataset['config'])
    config['single_generation']['max_attempts'] = 1
    app.state.dataset.set_config(config, app.state.dataset['revision'])
    runner.start('custom')
    await runner.job
    assert len(wire) == 1
    assert runner.status['phase'] == ('complete' if valid else 'rejected')
    assert runner.scenario.flow.schemas['Action'] == schema
    proposal = runner.snapshot['content_requests'][0]
    assert proposal['output_mode'] == 'native'
    assert proposal['local_schema_constraints'] == [
        {'keyword': k, 'schema_path': f'/allOf/0/{k}'} for k in ('if','then','else')]
    assert proposal['metrics']['local_schema_keywords'] == ['else','if','then']
    if not valid:
        assert runner.snapshot['steps']['decide']['error'] == 'ValidationError'
        assert json.loads(proposal['raw_output']) == result
        if mode == 'content-first':
            sample = app.state.dataset['samples'][0]
            assert sample['metadata']['validation_level'] == 0
            assert sample['metadata']['generation_record']['failures']
            assert not compiler.compile(sample)['tool_calls']
