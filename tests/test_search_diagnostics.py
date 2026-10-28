"""Compiler diagnostic filters share SQL/draft semantics and refresh safely."""

import copy

import pytest

from trajectory_remix.search import SearchQuery
from trajectory_remix.storage import DatasetStore, SQLiteSamples
from trajectory_remix.tools import ToolRegistry


def diagnostic_samples(sample):
    warning = copy.deepcopy(sample)
    warning['id'] = 'warning'
    clean = copy.deepcopy(sample)
    clean['id'] = 'clean'
    clean['replay']['1'] = [
        {'op': 'generate', 'window': window, 'content': '<|eos|>'}
        for window in ('a', 'b')
    ]
    error = copy.deepcopy(sample)
    error['id'] = 'error'
    error['replay']['1'] = [{'op': 'unknown', 'window': 'a'}]
    return [warning, clean, error]


@pytest.fixture
def diagnostic_store(compiler, sample, tmp_path):
    store = DatasetStore.initialize(tmp_path / 'diagnostics.sqlite', compiler)
    store.replace(diagnostic_samples(sample), 0)
    return store


@pytest.mark.parametrize('query,expected', [
    ('@errors:true', ['error']),
    ('@errors:false', ['warning', 'clean']),
    ('@warnings:true', ['warning']),
    ('@warnings:false', ['clean', 'error']),
    ('@ERRORS:TrUe', ['error']),
    ('@WARNINGS:FaLsE', ['clean', 'error']),
    ('-@errors:true', ['warning', 'clean']),
    ('-@warnings:false', ['warning']),
    ('@errors:false @warnings:false', ['clean']),
    ('@errors:true | @warnings:true', ['warning', 'error']),
    ('-(@errors:true | @warnings:true)', ['clean']),
    ('(@errors:true | @warnings:true) @name:warn*', ['warning']),
    ('@errors:true @warnings:true', []),
])
def test_diagnostics_sql_and_drafts_agree(diagnostic_store, query, expected):
    store = diagnostic_store
    before = store.info()
    sources = list(store.samples)
    assert [s['id'] for s in sources if SearchQuery(query).matches(s, store.load_compiler())] == expected
    total, rows = store.listing(query)
    assert total == len(expected)
    assert [s['id'] for s in rows] == expected
    assert store.info() == before
    assert list(store.samples) == sources


def test_diagnostic_cache_is_lazy_bounded_and_refreshes(diagnostic_store, monkeypatch):
    store = diagnostic_store
    compiler = store.load_compiler()
    store.search_compiler = compiler
    original = compiler.compile
    compiled = []

    def track(sample, **kwargs):
        compiled.append(sample['id'])
        return original(sample, **kwargs)

    monkeypatch.setattr(compiler, 'compile', track)
    store.listing('@name:*')
    assert compiled == []
    assert store.listing('@errors:true | @warnings:true', offset=1, limit=1)[1][0]['id'] == 'error'
    assert compiled == ['warning', 'clean', 'error']
    store.listing('@errors:false @warnings:false')
    assert len(compiled) == 3

    fixed = store.samples[2]
    fixed['replay'] = store.samples[1]['replay']
    store.write([{'op': 'replace', 'path': '/2', 'value': fixed}], store.info()[0])
    assert store.listing('@errors:true')[0] == 0
    assert compiled == ['warning', 'clean', 'error', 'error']

    def forbidden(*args):
        raise AssertionError('Cached searches must not decode trajectory sources')

    monkeypatch.setattr(SQLiteSamples, '__iter__', forbidden)
    monkeypatch.setattr(SQLiteSamples, '__getitem__', forbidden)
    monkeypatch.setattr('trajectory_remix.storage.json.loads', forbidden)
    assert store.listing('@errors:false', dashboard=True, offset=3)[0] == 3


def test_diagnostic_cache_rechecks_on_reopen(diagnostic_store):
    store = diagnostic_store
    assert store.listing('@errors:true')[0] == 1
    with store.connect() as db:
        db.execute('UPDATE trajectory_search_diagnostics SET errors=0,warnings=0')
    assert DatasetStore(store.path).listing('@errors:true')[0] == 1


def test_failed_diagnostic_refresh_rolls_back(diagnostic_store, monkeypatch):
    import trajectory_remix.storage as storage

    store = diagnostic_store
    original = storage.diagnostic_flags

    def interrupted(sample, compiler):
        if sample['id'] == 'clean':
            raise RuntimeError('interrupted compilation')
        return original(sample, compiler)

    monkeypatch.setattr(storage, 'diagnostic_flags', interrupted)
    with pytest.raises(RuntimeError, match='interrupted compilation'):
        store.listing('@errors:true')
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM trajectory_search_diagnostics').fetchone()[0] == 0
    monkeypatch.setattr(storage, 'diagnostic_flags', original)
    assert store.listing('@errors:true')[0] == 1
    store.write([{'op': 'remove', 'path': '/2'}], store.info()[0])
    assert store.listing('@errors:true')[0] == 0
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM trajectory_search_diagnostics').fetchone()[0] == 2


def test_diagnostics_follow_custom_tool_changes_without_pinning(compiler, sample, tmp_path, monkeypatch):
    folder = tmp_path / 'tools'
    folder.mkdir()
    path = folder / 'custom.py'
    source = '''name = "custom"
invisible = FLAG
schema = {"type": "object"}
def run(args, state, windows):
    return "ok", True, False
'''
    path.write_text(source.replace('FLAG', 'False'))
    store = DatasetStore.initialize(tmp_path / 'tools.sqlite', compiler)
    store.configure_tools(ToolRegistry(folder))
    sample['replay']['1'] = [{'op': 'tool-call', 'window': 'tool', 'tool': 'custom', 'arguments': {}}]
    store.replace([sample], 0)

    def forbidden(*args):
        raise AssertionError('Search must not record tool compilations/dependencies')

    monkeypatch.setattr(store, 'record_tool_compilation', forbidden)
    assert store.listing('@errors:false @warnings:true')[0] == 1
    path.write_text(source.replace('FLAG', 'True'))
    store.configure_tools(ToolRegistry(folder))
    assert store.listing('@errors:true')[0] == 1
    assert store.listing('@warnings:true')[0] == 0


async def test_diagnostic_search_dashboard_mcp_and_undo(studio, sample):
    app, client = studio
    store = app.state.dataset.store
    store.replace(diagnostic_samples(sample), store.info()[0])

    async def check(query, expected):
        dashboard = await client.get('/api/state', params={'query': query, 'limit': 1})
        mcp = await client.post('/api/mcp/call', json={
            'name': 'list_trajectories', 'arguments': {'query': query, 'limit': 1},
        })
        assert dashboard.status_code == mcp.status_code == 200
        assert dashboard.json()['total'] == mcp.json()['total'] == len(expected)
        assert [s['id'] for s in dashboard.json()['samples']] == expected[:1]
        assert [s['id'] for s in mcp.json()['trajectories']] == expected[:1]

    await check('@errors:true | @warnings:true', ['warning', 'error'])
    await check('@errors:false @warnings:false', ['clean'])
    for name in ('errors', 'warnings'):
        assert (await client.get('/api/state', params={'query': f'@{name}:yes'})).status_code == 422
        invalid = await client.post('/api/mcp/call', json={
            'name': 'list_trajectories', 'arguments': {'query': f'@{name}:yes'},
        })
        assert invalid.status_code == 422
        assert f'@{name} requires true or false' in invalid.json()['detail']

    fixed = store.samples[2]
    fixed['replay'] = store.samples[1]['replay']
    response = await client.put('/api/samples/2', json={'sample': fixed})
    assert response.status_code == 200, response.text
    await check('@errors:true', [])
    await check('@warnings:false', ['clean', 'error'])
    for command, expected in [('undo_edits', ['error']), ('redo_edits', [])]:
        response = await client.post('/api/mcp/call', json={'name': command, 'arguments': {}})
        assert response.status_code == 200, response.text
        await check('@errors:true', expected)
