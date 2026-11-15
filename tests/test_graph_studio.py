"""Tiny manual-studio API integration checks. No models, training or benchmarks."""
import copy
import json
import time
import uuid
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app
from test_graph_compiler import profile


def source():
    return dict(format='kidra-trajectory-graph/1', id=str(uuid.uuid4()),
        windows={'doc': dict(purpose='document', agent_writable=True)},
        events={'a': dict(kind='external_text', window='doc', content='hello'),
                'b': dict(kind='agent_text', window='doc', content='world', after=['a'], close_window=True)},
        metadata={'stage': 3})


@pytest.fixture
def client(tmp_path):
    path = tmp_path / 'graph.sqlite'
    GraphStore.initialize(path, {'tokenizer_profiles': {'bytes': profile().model_dump(mode='json')}})
    app = create_studio_app(path, artifact_root=tmp_path / 'exports')
    with TestClient(app) as client:
        client.headers['X-Remix-Token'] = app.state.csrf
        yield client


def create(c):
    r = c.post('/api/v1/trajectories', json=source())
    assert r.status_code == 201, r.text
    return r.json()


def settled(c, job):
    for _ in range(200):
        r = c.get('/api/v1/jobs/' + job).json()
        if r['state'] in ('completed', 'paused', 'cancelled'):
            return r
        time.sleep(.01)
    pytest.fail('Tiny background job did not settle')


def test_local_transport_and_schema(client):
    assert client.get('/').status_code == 200
    assert client.get('/api/v1/project', headers={'X-Remix-Token': 'wrong'}).status_code == 403
    assert client.get('/api/v1/project', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/v1/project', headers={'Host': 'evil.example'}).status_code == 400
    assert client.get('/api/v1/project').json()['generation_available'] is False
    assert client.get('/api/v1/schema/source').json()['properties']['format']['const'] == 'kidra-trajectory-graph/1'


def test_graph_operations_never_construct_tokenizer(client, monkeypatch):
    from trajectory_remix.graph.compiler import GraphCompiler
    monkeypatch.setattr(GraphCompiler, '__init__', lambda *a, **k: pytest.fail('Graph mode compiled'))
    r = create(client); ident = r['source']['id']
    assert client.get('/api/v1/trajectories').json()['items'][0]['id'] == ident
    assert client.get(f'/api/v1/trajectories/{ident}/graph').json()['order'] == ['a', 'b']
    assert client.get(f'/api/v1/trajectories/{ident}/diagnostics').status_code == 200
    diagnosed = client.post('/api/v1/diagnostics', json=source()).json()
    assert diagnosed['source']['events']['a']['delivery']['chain_immediately'] is True
    assert client.get('/api/v1/trajectories').json()['total'] == 1
    settings = client.get('/api/v1/settings').json()
    assert client.put('/api/v1/settings', json={'expected_revision': settings['revision'], 'settings': settings['settings']}).status_code == 200
    r2 = client.patch(f'/api/v1/trajectories/{ident}', json={'expected_revision': 1, 'patch': {'metadata': {'split': 'Auto'}}}).json()
    assert r2['intrinsic_hash'] == r['intrinsic_hash']
    assert r2['intrinsic_revision'] == r['intrinsic_revision']
    assert r2['metadata_revision'] == r['metadata_revision'] + 1


def test_no_tokenizer_project(tmp_path):
    path = tmp_path / 'empty.sqlite'; GraphStore.initialize(path)
    from trajectory_remix.server import create_app
    with TestClient(create_app(path)) as c:
        c.headers['X-Remix-Token'] = c.app.state.csrf
        r = create(c)
        assert c.get('/api/v1/settings').json()['settings']['tokenizer_profiles'] == {}
        assert c.post(f"/api/v1/trajectories/{r['source']['id']}/preview", json={'expected_revision': 1, 'profile': 'missing'}).status_code == 422


def test_cas_locks_and_broken_reference_removal(client):
    r = create(client); ident = r['source']['id']; base = f'/api/v1/trajectories/{ident}'
    bad = client.patch(base, json={'expected_revision': 2, 'patch': {'metadata': {'stage': 0}}})
    assert bad.status_code == 409 and bad.json()['current_revision'] == 1
    jobs = client.app.state.jobs
    job = jobs.create_job('manual'); jobs.acquire(job, ident, 1)
    assert client.patch(base, json={'expected_revision': 1, 'patch': {}}).status_code == 409
    assert client.request('DELETE', base, json={'expected_revision': 1}).status_code == 409
    jobs.control(job, 'cancel')
    deleted = client.request('DELETE', base + '/events/a', json={'expected_revision': 1}).json()
    assert deleted['source']['events']['b']['after'] == ['a']
    assert deleted['has_errors']
    repaired = client.put(base + '/events/b/dependencies', json={'expected_revision': 2, 'after': []}).json()
    assert not repaired['has_errors']


def test_structured_field_errors_and_invalid_json(client):
    r = create(client); base = f"/api/v1/trajectories/{r['source']['id']}"
    assert client.patch(base, content='{').status_code == 422
    invalid = client.patch(base, json={'expected_revision': 1, 'patch': {'events': {'a': {'kind': 'unknown'}}}})
    assert invalid.status_code == 422
    assert invalid.json()['code'] == 'invalid_fields'
    assert 'events' in invalid.json()['errors'][0]['loc']
    assert client.get(base).json()['revision'] == 1
    settings = client.get('/api/v1/settings').json()
    settings['settings']['tokenizer_profiles']['broken'] = {'name': 'bad', 'definition': {}, 'special_tokens': {}}
    assert client.put('/api/v1/settings', json={'expected_revision': settings['revision'], 'settings': settings['settings']}).status_code == 422


def test_bounded_listing_and_shared_bulk_selector(client):
    ids = [create(client)['source']['id'] for _ in range(3)]
    listing = client.get('/api/v1/trajectories?limit=1&stage=3').json()
    assert listing['total'] == 3 and len(listing['items']) == 1
    assert 'source' not in listing['items'][0]
    # Explicit Auto and absent split remain distinct.
    client.patch('/api/v1/trajectories/' + ids[0], json={'expected_revision': 1, 'patch': {'metadata': {'split': 'Auto'}}})
    assert client.get('/api/v1/trajectories?split=Auto').json()['total'] == 1
    job = client.post('/api/v1/bulk', json={'query': {'stage': 3}, 'action': 'stage', 'config': {'value': 2}}).json()['job_id']
    assert settled(client, job)['state'] == 'completed'
    assert client.get('/api/v1/trajectories?stage=2').json()['total'] == 3
    deltas = client.get(f'/api/v1/jobs/{job}/history').json()
    assert len(deltas) == 3
    undo = client.post('/api/v1/bulk/history', json={'deltas': deltas, 'undo': True}).json()
    assert not undo['warnings'] and len(undo['deltas']) == 3
    assert client.post('/api/v1/bulk/history', json={'deltas': undo['deltas'], 'undo': False}).status_code == 200


def test_delete_restore_guard(client):
    r = create(client); ident = r['source']['id']
    delta = client.request('DELETE', f'/api/v1/trajectories/{ident}', json={'expected_revision': 1}).json()
    assert client.get(f'/api/v1/trajectories/{ident}').status_code == 404
    inverse = client.post('/api/v1/bulk/history', json={'deltas': [delta]}).json()
    assert client.get(f'/api/v1/trajectories/{ident}').json()['revision'] == 2
    assert client.post('/api/v1/bulk/history', json={'deltas': [delta]}).json()['warnings']
    client.post('/api/v1/bulk/history', json={'deltas': inverse['deltas'], 'undo': False})
    assert client.get(f'/api/v1/trajectories/{ident}').status_code == 404


def test_preview_draft_cache_identity_and_read_only(client, monkeypatch):
    r = create(client); ident = r['source']['id']; base = f'/api/v1/trajectories/{ident}'
    payload = {'expected_revision': 1, 'profile': 'bytes', 'client_key': 'draft-1'}
    first = client.post(base + '/preview', json=payload).json()
    assert first['source_hash'] == r['intrinsic_hash'] and first['frames']
    assert first['client_key'] == 'draft-1'
    from trajectory_remix.graph.compiler import GraphCompiler
    original = GraphCompiler.preview
    monkeypatch.setattr(GraphCompiler, 'preview', lambda *a, **kw: pytest.fail('Cache missed'))
    draft = copy.deepcopy(r['source']); draft['metadata'] = {'stage': -3}
    same = client.post(base + '/preview', json=payload | {'source': draft, 'client_key': 'draft-2'}).json()
    assert same['cache_key'] == first['cache_key'] and same['client_key'] == 'draft-2'
    monkeypatch.setattr(GraphCompiler, 'preview', original)
    draft['events']['a']['content'] = 'new'
    different = client.post(base + '/preview', json=payload | {'source': draft}).json()
    assert different['source_hash'] != first['source_hash']
    assert client.get(base).json()['revision'] == 1
    assert client.app.state.artifacts.artifacts.root.exists()
    assert not list(client.app.state.artifacts.artifacts.root.glob('export-*'))


def test_import_independent_jobs_and_compact_polling(client):
    s = source()
    imported = client.post('/api/v1/imports', json={'text': json.dumps(s) + '\n{}\n' + json.dumps(s)})
    assert imported.status_code == 202
    job = imported.json()['job_id']; assert settled(client, job)['state'] == 'completed'
    assert client.get('/api/v1/trajectories').json()['total'] == 1
    logs = client.get(f'/api/v1/jobs/{job}/logs').json()
    assert len(logs['items']) == 2
    summaries = client.get('/api/v1/job-summaries').json()
    assert all('config' not in j for j in summaries)
    assert summaries[0]['progress'] == 3
    assert client.post(f'/api/v1/jobs/{job}/resume').status_code == 409


def test_real_raw_compiled_and_log_downloads(client):
    create(client)
    for compiled in (False, True):
        r = client.post('/api/v1/exports', json={'profile': 'bytes' if compiled else None,
            'options': {'compiled': compiled, 'metadata': 'full', 'minimum_stage': 3}})
        assert r.status_code == 202
        job = r.json()['job_id']; assert settled(client, job)['state'] == 'completed'
        manifest = client.get(f'/api/v1/jobs/{job}/manifest').json()
        assert manifest['compiled'] is compiled and manifest['warnings']
        content = client.get(f'/api/v1/jobs/{job}/artifacts/download.zip')
        assert content.status_code == 200
        archive = zipfile.ZipFile(BytesIO(content.content))
        assert 'manifest.json' in archive.namelist()
        assert any(name.endswith('.ktremix' if compiled else '.jsonl') for name in archive.namelist())
    client.app.state.jobs.append_log(job, 'retained')
    download = client.post(f'/api/v1/logs/{job}/export').json()['job_id']
    assert client.get(f'/api/v1/jobs/{download}/artifacts/logs.txt').content == b'retained\n'
    assert client.delete('/api/v1/logs').status_code == 200
    assert client.get(f'/api/v1/jobs/{job}').json()['state'] == 'completed'


def test_agent_tasks_share_commands(client):
    tools = client.get('/api/mcp/tools').json()['tools']
    assert 'patch_graph_event' in {t['name'] for t in tools}
    assert all('sandbox' not in t['name'] for t in tools)
    r = client.post('/api/mcp/call', json={'name': 'create_graph', 'arguments': source()}).json()
    ident = r['source']['id']
    edited = client.post('/api/mcp/call', json={'name': 'patch_graph_event', 'arguments': {
        'id': ident, 'event_id': 'b', 'expected_revision': 1, 'patch': {'after': []}}})
    assert edited.status_code == 200
    assert client.get(f'/api/v1/trajectories/{ident}').json()['revision'] == 2
    for topic in ('start', 'dependencies', 'tools-chat', 'export'):
        assert client.get('/api/v1/agent/guide/' + topic).json()['text']


def test_graph_mcp_protocol_discovery_and_call(client):
    pytest.importorskip('mcp')
    headers = {'Accept': 'application/json, text/event-stream'}
    initialized = client.post('/mcp', headers=headers, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                   'clientInfo': {'name': 'graph-test', 'version': '1'}}})
    assert initialized.status_code == 200, initialized.text
    instructions = initialized.json()['result']['instructions']
    assert 'Manual graph studio' in instructions and 'tick keys' not in instructions
    headers.update({'Mcp-Session-Id': initialized.headers['mcp-session-id'], 'Mcp-Protocol-Version': '2025-03-26'})
    assert client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}).status_code == 202
    listing = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json()
    assert 'read_graph' in {t['name'] for t in listing['result']['tools']}
    called = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
        'params': {'name': 'get_graph_session', 'arguments': {}}}).json()
    assert called['result']['structuredContent']['generation_available'] is False


@pytest.mark.asyncio
async def test_import_pause_resume_and_cancel_fence(tmp_path):
    import asyncio
    from trajectory_remix.graph.jobs import JobService, Fenced
    from trajectory_remix.graph.bulk import BulkService
    from trajectory_remix.graph.studio import Studio
    store = GraphStore.initialize(tmp_path / 'imports.sqlite')
    jobs = JobService(store)
    studio = Studio(jobs, BulkService(jobs), None, None)
    lines = [json.dumps(source()) for _ in range(3)]
    job = jobs.create_job('import', {'lines': lines}, total=3)
    task = asyncio.create_task(studio.import_job(jobs, job))
    await asyncio.sleep(0)
    assert jobs.inspect(job)['progress'] == 1
    jobs.control(job, 'pause')
    with pytest.raises(Fenced):
        await task
    # Restart leaves the paused cursor and committed source alone.
    jobs = JobService(store)
    assert jobs.inspect(job)['state'] == 'paused'
    jobs.control(job, 'resume')
    await studio.import_job(jobs, job)
    assert jobs.inspect(job)['state'] == 'completed'
    assert studio.listing()['total'] == 3
    other = jobs.create_job('import', {'lines': [json.dumps(source()) for _ in range(3)]}, total=3)
    task = asyncio.create_task(studio.import_job(jobs, other)); await asyncio.sleep(0)
    jobs.control(other, 'cancel')
    with pytest.raises(Fenced):
        await task
    assert jobs.inspect(other)['progress'] == 1
    assert studio.listing()['total'] == 4


def test_export_diagnostic_is_actionable_and_has_no_partial_download(client):
    broken = source(); broken['events']['b']['after'] = ['missing']
    assert client.post('/api/v1/trajectories', json=broken).json()['has_errors']
    job = client.post('/api/v1/exports', json={'profile': 'bytes'}).json()['job_id']
    assert settled(client, job)['state'] == 'paused'
    logs = client.get(f'/api/v1/jobs/{job}/logs').json()['items']
    assert any('Export blocked:' in line['text'] and 'missing' in line['text'] for line in logs)
    assert client.get(f'/api/v1/jobs/{job}/artifacts/download.zip').status_code == 404


def test_current_tool_diagnostics_follow_settings_but_history_stays_pinned(client):
    s = source()
    s['events'] = {'call': dict(kind='tool_call', window='doc', tool='echo', call_id='one', content='{}')}
    r = client.post('/api/v1/trajectories', json=s).json()
    ident = r['source']['id']
    settings = client.get('/api/v1/settings').json()
    settings['settings']['tools']['echo'] = dict(name='Echo', input_schema={
        'type':'object', 'properties':{'needed':{'type':'string'}}, 'required':['needed']})
    assert client.put('/api/v1/settings', json={'settings':settings['settings'], 'expected_revision':settings['revision']}).status_code == 200
    current = client.get(f'/api/v1/trajectories/{ident}').json()
    historical = client.get(f'/api/v1/trajectories/{ident}?revision=1').json()
    assert current['source'] == historical['source']
    assert current['diagnostics'] != historical['diagnostics']
    assert any(d['severity'] == 'warning' and 'schema' in d['code'] for d in current['diagnostics'])


def test_bounded_agent_jobs_controls_logs_results_and_downloads(client):
    def call(name, **arguments):
        r = client.post('/api/mcp/call', json=dict(name=name, arguments=arguments))
        assert r.status_code == 200, r.text
        return r.json()
    session = call('get_graph_session')
    assert 'settings' not in session and session['source_format'] == 'kidra-trajectory-graph/1'
    jobs = client.app.state.jobs
    job = jobs.create_run({'large': 'frozen'}, [{'large': 'proposal'} for _ in range(3)])
    summary = call('inspect_graph_job', id=job)
    assert not {'config','checkpoint'} & summary.keys()
    rows = call('graph_job_items', id=job, limit=2)
    assert len(rows['items']) == 2 and rows['next_offset'] == 2
    assert all('proposal' not in r for r in rows['items'])
    assert len(call('graph_job_items', id=job, offset=2)['items']) == 1
    jobs.append_log(job, 'x'*3000)
    logs = call('graph_job_logs', id=job)
    assert logs['items'][0]['line_truncated'] and len(logs['items'][0]['text']) == 2000
    assert call('control_graph_job', id=job, action='pause')['state'] == 'paused'
    assert call('control_graph_job', id=job, action='cancel')['state'] == 'cancelled'
    assert client.get(f'/api/v1/jobs/{job}?include_config=false').json() == call('inspect_graph_job', id=job)
    create(client)
    exported = call('export_graphs', options={'compiled': False})['job_id']
    assert settled(client, exported)['state'] == 'completed'
    files = call('graph_job_artifacts', id=exported)['files']
    assert any(f['name'] == 'download.zip' for f in files)
    for entry in files:
        assert client.get(entry['path']).status_code == 200
