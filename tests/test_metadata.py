import copy
import json
import sqlite3
import uuid

import httpx
import pytest

from trajectory_remix.compiler import Compiler
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore, SQLiteSamples


@pytest.fixture
async def metadata_studio(tmp_path, compiler, sample):
    path = tmp_path / "project.sqlite"
    profile = Compiler(compiler.tokenizer, max_window_slots=4)
    store = DatasetStore.initialize(path, profile)
    records = []
    for name, metadata in [
        ("a", {"domain": "code", "quality": 0.9, "tokens": 1234, "enabled": False, "optional": None, 'a"b/~': "literal", "tags": ["reviewed", "code", "code"]}),
        ("b", {"domain": "code", "quality": 0.4, "enabled": 0, "tags": ["code"]}),
        ("c", {"domain": "chat", "quality": 0.8, "tags": ["reviewed"]}),
    ]:
        records.append({**copy.deepcopy(sample), "id": name, "metadata": metadata})
    store.replace(records, 0)
    app = create_app(path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        yield store, app, client


async def query(studio, **arguments):
    response = await studio[2].post("/api/metadata/query", json=arguments)
    assert response.status_code == 200, response.text
    return response.json()


async def test_metadata_filters_tags_counts_and_pagination_are_sql_only(metadata_studio, monkeypatch):
    def fail(*args):
        raise AssertionError("Metadata queries must not load trajectory sources")

    monkeypatch.setattr(SQLiteSamples, "__getitem__", fail)
    monkeypatch.setattr(SQLiteSamples, "__iter__", fail)
    result = await query(metadata_studio, filters=[{"key": "quality", "op": "gte", "value": 0.8}], tags=["code"])
    assert result["total"] == 1
    row = result["trajectories"][0]
    assert row["id"] == "a"
    assert str(uuid.UUID(row["uuid"])) == row["uuid"]
    assert len(row["content_hash"]) == 64
    assert row["counts"] == {"events": 2, "windows": 2, "tags": 2}
    assert row["tags"] == ["reviewed", "code"]
    assert "replay" not in row
    assert (await query(metadata_studio, tags=["reviewed", "code"]))["total"] == 1
    assert (await query(metadata_studio, tags=["reviewed", "code"], tag_mode="any"))["total"] == 3
    page = await query(metadata_studio, offset=1, limit=1)
    assert page["total"] == 3 and page["trajectories"][0]["id"] == "b"
    response = await metadata_studio[2].post("/api/metadata/counts", json={"key": "tags"})
    assert response.status_code == 200, response.text
    assert {g["value"]: g["count"] for g in response.json()["groups"]} == {"code": 2, "reviewed": 2}


@pytest.mark.parametrize("filters,ids", [
    ([{"key": "enabled", "value": False}], ["a"]),
    ([{"key": "enabled", "value": 0}], ["b"]),
    ([{"key": "optional", "value": None}], ["a"]),
    ([{"key": "optional", "op": "exists"}], ["a"]),
    ([{"key": "tokens", "op": "gte", "value": 1000}], ["a"]),
    ([{"key": "quality", "op": "lt", "value": 0.5}], ["b"]),
    ([{"key": "counts.tags", "op": "gte", "value": 2}], ["a"]),
    ([{"key": 'a"b/~', "value": "literal"}], ["a"]),
    ([{"key": "x') OR 1=1 --", "op": "exists"}], []),
])
async def test_metadata_filters_preserve_json_types_and_literal_keys(metadata_studio, filters, ids):
    result = await query(metadata_studio, filters=filters)
    assert [s["id"] for s in result["trajectories"]] == ids


async def test_grouped_counts_and_mcp_share_the_metadata_projection(metadata_studio):
    _, _, client = metadata_studio
    response = await client.post("/api/mcp/call", json={"name": "get_metadata_counts", "arguments": {"key": "domain"}})
    assert response.status_code == 200, response.text
    assert {g["value"]: g["count"] for g in response.json()["groups"]} == {"code": 2, "chat": 1}
    response = await client.post("/api/metadata/counts", json={"key": "counts.windows", "tags": ["reviewed"], "limit": 1})
    assert response.json()["groups"] == [{"value": 2, "type": "integer", "count": 2}]
    response = await client.post("/api/mcp/call", json={"name": "query_trajectories", "arguments": {"filters": [{"key": "domain", "value": "code"}], "limit": 1}})
    assert response.status_code == 200 and response.json()["total"] == 2
    assert len(response.json()["trajectories"]) == 1


async def test_uuid_seed_hash_and_metadata_survive_edits_undo_and_delete(metadata_studio):
    store, _, client = metadata_studio
    source = (await client.get("/api/samples/0")).json()
    ident = source["uuid"]
    original = (await query(metadata_studio, tags=["code", "reviewed"]))["trajectories"][0]
    original_slots = store.load_compiler().compile(source)["window_slots"]
    source["id"] = "renamed-label"
    source["metadata"] = {"domain": "renamed", "tags": ["fresh"], "quality": 1.0}
    response = await client.put("/api/samples/0", json={"sample": source})
    assert response.status_code == 200, response.text
    changed = (await query(metadata_studio, tags=["fresh"]))["trajectories"][0]
    assert changed["uuid"] == ident and changed["content_hash"] != original["content_hash"]
    assert changed["counts"]["tags"] == 1
    assert store.load_compiler().compile(source)["window_slots"] == original_slots
    source["uuid"] = str(uuid.uuid4())
    assert (await client.put("/api/samples/0", json={"sample": source})).status_code == 422
    response = await client.post("/api/mcp/call", json={"name": "undo_edits", "arguments": {}})
    assert response.status_code == 200, response.text
    restored = (await query(metadata_studio, tags=["code", "reviewed"]))["trajectories"][0]
    assert restored == original
    assert (await client.delete("/api/samples/0")).status_code == 200
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM trajectory_metadata WHERE trajectory_uuid=?", (ident,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM metadata_tags WHERE trajectory_uuid=?", (ident,)).fetchone()[0] == 0
    assert (await client.post("/api/mcp/call", json={"name": "undo_edits", "arguments": {}})).status_code == 200
    assert (await query(metadata_studio, tags=["code", "reviewed"]))["trajectories"][0] == original


async def test_import_uuid_conflict_rolls_back_metadata_and_counts(metadata_studio):
    store, _, client = metadata_studio
    before = await query(metadata_studio)
    existing = store.samples[0]
    response = await client.post("/api/import", json={"files": [{
        "name": "batch.json", "text": json.dumps([
            {"id": "new", "metadata": {"tags": ["should-rollback"]}},
            {**existing, "id": "duplicate-uuid"},
        ]),
    }]})
    assert response.status_code == 422
    assert await query(metadata_studio) == before
    assert (await query(metadata_studio, tags=["should-rollback"]))["total"] == 0


async def test_uuid_is_immutable_through_mcp_and_sql_and_duplicates_get_new_identity(metadata_studio):
    store, _, client = metadata_studio
    original = store.samples[0]
    for name, arguments in [
        ("update_trajectory_metadata", {"sample_id": "a", "patch": {"uuid": str(uuid.uuid4())}}),
        ("replace_trajectory", {"sample_id": "a", "sample": {**original, "uuid": str(uuid.uuid4())}}),
    ]:
        response = await client.post("/api/mcp/call", json={"name": name, "arguments": arguments})
        assert response.status_code == 422 and "immutable" in response.text
        assert store.samples[0] == original
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store.connect() as db:
            db.execute("UPDATE samples SET uuid=? WHERE uuid=?", (str(uuid.uuid4()), original["uuid"]))
    response = await client.post("/api/mcp/call", json={"name": "duplicate_trajectory", "arguments": {"sample_id": "a", "new_sample_id": "copy"}})
    assert response.status_code == 200, response.text
    duplicated = store.samples.find("copy")[1]
    assert duplicated["uuid"] != original["uuid"]
    assert duplicated["metadata"] == original["metadata"]
    assert (await query(metadata_studio, tags=["reviewed", "code"]))["total"] == 2


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"offset": -1}, {"tag_mode": "unknown"},
    {"filters": [{"key": "quality", "op": "drop-table", "value": 1}]},
    {"filters": [{"key": "quality", "value": {"not": "scalar"}}]},
])
async def test_metadata_query_arguments_are_validated(metadata_studio, arguments):
    assert (await metadata_studio[2].post("/api/metadata/query", json=arguments)).status_code == 422
