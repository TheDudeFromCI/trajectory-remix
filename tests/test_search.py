import copy
import sqlite3

import pytest

from trajectory_remix.search import SearchQuery
from trajectory_remix.storage import DatasetStore, SQLiteSamples


@pytest.fixture
def search_store(compiler, tmp_path):
    store = DatasetStore.initialize(tmp_path / "search.sqlite", compiler)
    store.replace([
        {"id": "parallel-window-one", "metadata": {"tags": ["Coding", "linear-math"], "json_key_only": "Straße"},
         "replay": {"0": [*[{'op': 'open', 'window': f'w{i}'} for i in range(6)],
                          {"op": "append", "window": "w0", "content": "Python math list human apple a.b a?b [abc]"},
                          {"op": "tool-call", "window": "w1", "tool": "calculator", "arguments": {"text": "java"}},
                          {"op": "invisible-tool-call", "tool": "file_write", "arguments": {"content": "hidden-needle"}}]}},
        {"id": "parallel-window-two", "metadata": {"tags": ["#coding", "science"]},
         "replay": {"0": [{"op": "open", "window": "w0"},
                          {"op": "append", "window": "w0", "segments": [{"text": "PY"}, {"text": "THON math java humans banana"}]},
                          {"op": "tool-call", "window": "implicit", "tool": "file_read", "outcome": {"result": {"text": "outcome-needle"}}}]}},
        {"id": "other", "metadata": {"tags": ["code"]},
         "replay": {"0": [{"op": "open", "window": "w0"},
                          {"op": "append", "window": "w0", "content": "python complex java list humankind apple banana (example)"},
                          {"op": "show_tools", "tools": ["calculator"]}]}},
        {"id": "both-excluded", "replay": {"0": [{"op": "append", "content": "python math complex superhuman orange #coding"}]}},
        {"id": "my trajectory", "metadata": {"note": "ruby\njava list\n-complex\na|b"}, "replay": {}},
        {"replay": {}},
    ], 0)
    return store


@pytest.mark.parametrize("query,indices", [
    ("", [0, 1, 2, 3, 4, 5]),
    (" \t\n ", [0, 1, 2, 3, 4, 5]),
    ("pYtHoN", [0, 1, 2, 3]),
    ("java list", [0, 2, 4]),
    ("java\tlist", [0, 2, 4]),
    ("-complex", [0, 1, 5]),
    ("python -(math complex)", [0, 1, 2]),
    ("python -math -complex", []),
    ("apple | banana", [0, 1, 2]),
    ("apple|banana", [0, 1, 2]),
    ("apple | banana -complex", [0, 1, 2]),
    ("(apple | banana) -complex", [0, 1]),
    ("-(apple | banana)", [3, 4, 5]),
    ("--python", [0, 1, 2, 3]),
    ("((python) -(math (complex)))", [0, 1, 2]),
    ("human*", [0, 1, 2]),
    ("a*e", [0, 2]),
    ("*human*", [0, 1, 2, 3]),
    ("h*man*", [0, 1, 2]),
    ("a.b", [0]),
    ("a?b", [0]),
    ("[abc]", [0]),
    ("a.*", [0]),
    ("a?*", [0]),
    ("[a*", [0]),
    ("a*banana", []),
    ('"java list"', [2, 4]),
    ('"(example)"', [2]),
    ('"a|b"', [4]),
    ('"-complex"', [4]),
    ("strasse", [0]),
    ("json_key_only", []),
    ("segments", []),
    ("hidden-needle", [0]),
    ("outcome-needle", [1]),
    ("@name:PARALLEL-window-*", [0, 1]),
    ("@name:parallel-window-", []),
    ("@name:*window*", [0, 1]),
    ('@name:"my trajectory"', [4]),
    ("-@name:*", [5]),
    ("@windows:>5", [0]),
    ("@windows:>=2", [0, 1]),
    ("@windows:2", [1]),
    ("@windows:=1", [2]),
    ("@windows:<=1", [2, 3, 4, 5]),
    ("@windows:<1", [3, 4, 5]),
    ("@windows:!=0", [0, 1, 2]),
    ("@uses_tool:calculator", [0]),
    ("@uses_tool:FILE_*", [0, 1]),
    ("-@uses_tool:*", [2, 3, 4, 5]),
    ("#coding", [0, 1]),
    ("#code", [2]),
    ("#*math*", [0]),
    ("-#*math*", [1, 2, 3, 4, 5]),
    ("(#coding | #code) -@windows:>5", [1, 2]),
    ("#coding #science", [1]),
    ("python -(@windows:>5 @uses_tool:calculator)", [1, 2, 3]),
    ('"x\') OR 1=1 --"', []),
])
def test_search_sql_and_in_memory_semantics(search_store, query, indices):
    sources = list(search_store.samples)
    expected = [sources[i].get("id") for i in indices]
    total, result = search_store.listing(query)
    assert total == len(indices)
    assert [s.get("id") for s in result] == expected
    assert all("replay" not in row for row in result)
    assert [s.get("id") for s in sources if SearchQuery(query).matches(s)] == expected


def test_uuid_filters_and_filtering_before_pagination(search_store, monkeypatch):
    ident = search_store.samples[1]["uuid"]
    assert search_store.listing(f"@uuid:{ident.upper()}")[1][0]["id"] == "parallel-window-two"
    assert search_store.listing(f"@uuid:{ident[:15]}*")[0] == 1
    assert search_store.listing(f"-@uuid:{ident}")[0] == 5

    def forbidden(*args):
        raise AssertionError("Search must not decode trajectories")

    monkeypatch.setattr(SQLiteSamples, "__iter__", forbidden)
    monkeypatch.setattr(SQLiteSamples, "__getitem__", forbidden)
    total, page = search_store.listing("python -(math complex)", offset=2, limit=1, dashboard=True)
    assert total == 3 and page[0]["index"] == 2
    assert page[0]["id"] == "other"
    assert search_store.listing("#coding @uses_tool:file_*", offset=1, limit=1)[1][0]["id"] == "parallel-window-two"


@pytest.mark.parametrize("old_version", [3, 4])
def test_existing_projects_rebuild_search_once_without_source_or_revision_changes(search_store, old_version, monkeypatch):
    with search_store.connect() as db:
        before = db.execute("SELECT source,content_hash FROM samples ORDER BY position").fetchall()
        revision = search_store.info()[:3]
        db.execute("UPDATE project SET version=?", (old_version,))
        db.execute("UPDATE trajectory_metadata SET search='old-index'")
        db.execute("DROP TABLE trajectory_search_tools")
    upgraded = DatasetStore(search_store.path)
    assert upgraded.listing("hidden-needle @uses_tool:file_write")[0] == 1
    assert upgraded.listing("python -(math complex)")[0] == 3
    assert upgraded.info()[:3] == revision
    with upgraded.connect() as db:
        assert db.execute("SELECT source,content_hash FROM samples ORDER BY position").fetchall() == before
        assert db.execute("SELECT version FROM project").fetchone()[0] == 5

    def forbidden(*args):
        raise AssertionError("Ordinary startup must not rebuild search")

    monkeypatch.setattr("trajectory_remix.storage.readable_text", forbidden)
    assert DatasetStore(search_store.path).listing("human*")[0] == 3


def test_search_projection_migration_is_atomic(search_store, monkeypatch):
    import trajectory_remix.storage as storage

    with search_store.connect() as db:
        db.execute("UPDATE project SET version=4")
        db.execute("UPDATE trajectory_metadata SET search='old-index'")
        db.execute("DELETE FROM trajectory_search_tools")
    original = storage.readable_text

    def fail_second(sample):
        if sample.get("id") == "parallel-window-two":
            raise ValueError("interrupted migration")
        return original(sample)

    monkeypatch.setattr(storage, "readable_text", fail_second)
    with pytest.raises(ValueError, match="interrupted migration"):
        DatasetStore(search_store.path)
    with sqlite3.connect(search_store.path) as db:
        assert db.execute("SELECT version FROM project").fetchone()[0] == 4
        assert set(db.execute("SELECT search FROM trajectory_metadata")) == {("old-index",)}
        assert db.execute("SELECT count(*) FROM trajectory_search_tools").fetchone()[0] == 0
    monkeypatch.setattr(storage, "readable_text", original)
    assert DatasetStore(search_store.path).listing("hidden-needle")[0] == 1


async def test_dashboard_and_mcp_share_search_and_guide(studio):
    app, client = studio
    app.state.dataset.store.samples.append({
        "id": "parallel-window-search", "metadata": {"tags": ["coding"]},
        "replay": {"0": [{"op": "open", "window": "a"},
                          {"op": "append", "window": "a", "content": "Python math human"}]}},
    )
    query = "python -(math complex) #coding @name:parallel-window-* @windows:1"
    dashboard = await client.get("/api/state", params={"query": query})
    mcp = await client.post("/api/mcp/call", json={"name": "list_trajectories", "arguments": {"query": query}})
    assert dashboard.status_code == mcp.status_code == 200
    assert dashboard.json()["total"] == mcp.json()["total"] == 1
    assert dashboard.json()["samples"][0]["id"] == mcp.json()["trajectories"][0]["id"]
    assert dashboard.json()["samples"][0]["index"] == 1
    assert (await client.get("/api/state", params={"query": "python |"})).status_code == 422
    invalid = await client.post("/api/mcp/call", json={"name": "list_trajectories", "arguments": {"query": "python |"}})
    assert invalid.status_code == 422 and "Invalid search query" in invalid.json()["detail"]
    before = app.state.dataset["revision"]
    guide = await client.post("/api/mcp/call", json={"name": "get_authoring_guide", "arguments": {"topic": "search"}})
    assert guide.status_code == 200 and "## Wildcards" in guide.json()["content"]
    assert (await client.get("/search-guide")).text == guide.json()["content"]
    assert app.state.dataset["revision"] == before


async def test_edits_undo_redo_deletion_and_failed_import_update_search_atomically(studio):
    app, client = studio

    async def count(query):
        result = await client.get("/api/state", params={"query": query})
        assert result.status_code == 200, result.text
        return result.json()["total"]

    sample = (await client.get("/api/samples/0")).json()
    sample["replay"]["0"].extend([
        {"op": "append", "window": "a", "content": "durable-needle"},
        {"op": "invisible-tool-call", "tool": "file_write", "arguments": {"path": "/a", "content": "hi"}},
    ])
    sample["metadata"] = {"tags": ["new-tag"]}
    assert (await client.put("/api/samples/0", json={"sample": sample})).status_code == 200
    query = "durable-needle #new-tag @uses_tool:file_write"
    assert await count(query) == 1
    for name, expected in [("undo_edits", 0), ("redo_edits", 1)]:
        assert (await client.post("/api/mcp/call", json={"name": name, "arguments": {}})).status_code == 200
        assert await count(query) == expected
    duplicate = await client.post("/api/mcp/call", json={"name": "duplicate_trajectory", "arguments": {"sample_id": "test", "new_sample_id": "copy"}})
    assert duplicate.status_code == 200, duplicate.text
    assert await count(query) == 2
    assert (await client.delete("/api/samples/1")).status_code == 200
    assert await count(query) == 1
    with app.state.dataset.store.connect() as db:
        assert db.execute("SELECT count(*) FROM trajectory_search_tools").fetchone()[0] == 1
    import json
    invalid = await client.post("/api/import", json={"files": [{"name": "invalid.json", "text": json.dumps([
        {"id": "new", "replay": {"0": [{"op": "invisible-tool-call", "tool": "calculator", "arguments": {"text": "rollback-needle"}}]}},
        copy.deepcopy(sample),
    ])}]})
    assert invalid.status_code == 422
    assert await count("rollback-needle | @uses_tool:calculator") == 0
    assert await count(query) == 1


@pytest.fixture
def review_store(search_store):
    samples = []
    for level, name in enumerate(
        ['unfinished', 'rejected', 'unsuccessful', 'pending', 'candidate', 'validated', 'ready'], -3
    ):
        samples.append({
            'id': name,
            'metadata': {'validation_level': level, 'generation_batch': 'Batch-A' if level <= 1 else 'Batch-B'},
            'replay': {},
        })
    samples.extend([
        {'id': 'missing', 'replay': {}},
        {'id': 'null', 'metadata': {'validation_level': None, 'generation_batch': None}, 'replay': {}},
        {'id': 'text-only', 'metadata': {'note': 'candidate Batch-A'}, 'replay': {}},
    ])
    search_store.replace(samples, search_store.info()[0])
    return search_store


@pytest.mark.parametrize('query,expected', [
    *[(f'@state:{name.upper()}', [name]) for name in
      ['rejected', 'unsuccessful', 'pending', 'candidate', 'validated', 'ready']],
    *[(f'@state:{level}', [name]) for level, name in enumerate(
      ['rejected', 'unsuccessful', 'pending', 'candidate', 'validated', 'ready'], -2)],
    ('@state:unfinished', ['unfinished', 'missing', 'null', 'text-only']),
    ('@state:-3', ['unfinished', 'missing', 'null', 'text-only']),
    ('@batch:BATCH-A', ['unfinished', 'rejected', 'unsuccessful', 'pending', 'candidate']),
    ('@batch:batch-*', ['unfinished', 'rejected', 'unsuccessful', 'pending', 'candidate', 'validated', 'ready']),
    ('-@batch:*', ['missing', 'null', 'text-only']),
    ('@state:candidate @batch:batch-a', ['candidate']),
    ('(@state:candidate | @state:validated) -@batch:batch-a', ['validated']),
    ('-@state:unfinished -@state:rejected', ['unsuccessful', 'pending', 'candidate', 'validated', 'ready']),
    ('@batch:"x\' OR 1=1 --"', []),
])
def test_review_discovery_sql_and_draft_parity(review_store, query, expected):
    total, result = review_store.listing(query)
    assert total == len(expected)
    assert [row['id'] for row in result] == expected
    assert [sample['id'] for sample in review_store.samples if SearchQuery(query).matches(sample)] == expected


def test_review_discovery_uses_metadata_before_pagination(review_store, monkeypatch):
    def forbidden(*args):
        raise AssertionError('Review discovery must not decode trajectory sources')

    monkeypatch.setattr(SQLiteSamples, '__iter__', forbidden)
    monkeypatch.setattr(SQLiteSamples, '__getitem__', forbidden)
    total, page = review_store.listing('@batch:batch-a -@state:unfinished', offset=1, limit=2)
    assert total == 4
    assert [row['id'] for row in page] == ['unsuccessful', 'pending']


async def test_candidate_discovery_through_mcp_and_dashboard(studio):
    app, client = studio
    for i in range(7):
        app.state.dataset.store.samples.append({
            'id': f'candidate-{i}',
            'metadata': {'validation_level': 1, 'generation_batch': 'review-batch'},
            'replay': {},
        })
    query = '@state:candidate @batch:review-batch'
    mcp = await client.post('/api/mcp/call', json={
        'name': 'list_trajectories', 'arguments': {'query': query, 'limit': 5},
    })
    dashboard = await client.get('/api/state', params={'query': query, 'limit': 5})
    assert mcp.status_code == dashboard.status_code == 200
    assert mcp.json()['total'] == dashboard.json()['total'] == 7
    assert [s['id'] for s in mcp.json()['trajectories']] == [f'candidate-{i}' for i in range(5)]
    assert [s['id'] for s in dashboard.json()['samples']] == [f'candidate-{i}' for i in range(5)]
    assert (await client.post('/api/samples/1/state', json={'level': 2})).status_code == 200
    remaining = await client.post('/api/mcp/call', json={
        'name': 'list_trajectories', 'arguments': {'query': query, 'limit': 5},
    })
    assert remaining.json()['total'] == 6
    assert 'candidate-0' not in [s['id'] for s in remaining.json()['trajectories']]
