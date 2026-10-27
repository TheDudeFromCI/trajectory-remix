import json
import subprocess
import sys
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from trajectory_remix.llm import default_config
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore, SQLiteSamples, StorageConflict


def test_concurrent_writers_preserve_entries_and_reject_stale_edits(tmp_path, compiler):
    path = tmp_path / "project.sqlite"
    store = DatasetStore.initialize(path, compiler)

    def write_batch(writer):
        other = DatasetStore(path)
        for number in range(20):
            # Separate connections serialize the count+append in one transaction.
            with other.transaction() as db:
                count = db.execute("SELECT sample_count FROM project").fetchone()[0]
                other.insert(db, count, {"id": f"{writer}-{number}"})
                db.execute("UPDATE project SET sample_revision=revision+1,revision=revision+1")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_batch, range(4)))
    assert len(store.samples) == 80
    assert store.info()[0] == 80
    draft = store.samples.copy()
    draft[0] = {"id": "stale"}
    store.samples.append({"id": "new"})
    with pytest.raises(StorageConflict):
        store.write(draft.operations, draft.revision)
    assert len(store.samples) == 81
    assert store.samples[0]["id"] != "stale"


def test_transactions_rollback_and_snapshot_reads_allow_writes(tmp_path, compiler):
    store = DatasetStore.initialize(tmp_path / "project.sqlite", compiler)
    store.replace([{"id": "a"}, {"id": "b"}], 0)
    revision = store.info()[0]
    with pytest.raises(ValueError, match="Duplicate"):
        store.replace([{"id": "duplicate"}, {"id": "duplicate"}], revision)
    assert store.info()[0] == revision
    with store.read_samples() as records:
        assert next(records)["id"] == "a"
        other = DatasetStore(store.path)
        other.samples.append({"id": "c"})
        assert [s["id"] for s in records] == ["b"]
    assert len(store.samples) == 3
    with store.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_sparse_middle_inserts_rebalance_without_changing_order(tmp_path, compiler):
    store = DatasetStore.initialize(tmp_path / "project.sqlite", compiler)
    store.replace([{"id": "first"}, {"id": "last"}], 0)
    for number in range(70):
        store.samples.insert(1, {"id": f"middle-{number}"})
    assert [s["id"] for s in store.samples] == ["first", *[f"middle-{n}" for n in reversed(range(70))], "last"]


async def test_large_dataset_stays_lazy_during_startup_polling_and_edits(compiler, tmp_path, monkeypatch):
    path = tmp_path / "large.sqlite"
    store = DatasetStore.initialize(path, compiler)
    count = 100_005
    with store.transaction() as db:
        db.execute("""
            WITH RECURSIVE numbers(n) AS (
                VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n < ?
            )
            INSERT INTO samples(uuid,position,sample_id,source,attributes,content_hash)
            SELECT printf('%08x-0000-4000-8000-%012x',n,n), n*4294967296, 's-'||n,
                json_object('id', 's-'||n, 'uuid', printf('%08x-0000-4000-8000-%012x',n,n), 'replay', json('{}'), 'payload', printf('%0100d',n)),
                json_object('id', 's-'||n, 'uuid', printf('%08x-0000-4000-8000-%012x',n,n)), 'fixture'
            FROM numbers
        """, (count,))
        db.execute("INSERT INTO trajectory_metadata SELECT uuid,NULL,'[]',0,0,0,sample_id FROM samples")
        db.execute("UPDATE project SET sample_count=?", (count,))

    def forbid_full_scan(self):
        raise AssertionError("Runtime attempted to iterate the dataset")

    monkeypatch.setattr(SQLiteSamples, "__iter__", forbid_full_scan)
    tracemalloc.start()
    try:
        app = create_app(path)
        assert isinstance(app.state.dataset["samples"], SQLiteSamples)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver",
            headers={"X-Remix-Token": app.state.csrf},
        ) as client:
            state = (await client.get("/api/state")).json()
            assert state["sample_count"] == count
            assert len(state["samples"]) == 100
            assert all("replay" not in s for s in state["samples"])
            page = (await client.get("/api/state?offset=100000&limit=10")).json()
            assert [s["index"] for s in page["samples"]] == list(range(100000, count))
            source = (await client.get(f"/api/samples/{count-1}")).json()
            source["metadata"] = {"tags": ["tail"]}
            edit = await client.put(f"/api/samples/{count-1}", json={"sample": source, "expected_revision": 0})
            assert edit.status_code == 200, edit.text
            found = (await client.get("/api/state?query=tail")).json()
            assert found["total"] == 1 and found["samples"][0]["index"] == count-1
            batch = await client.post("/api/mcp/call", json={
                "name": "apply_batch", "arguments": {"validate": False, "edits": [{
                    "name": "update_trajectory_metadata",
                    "arguments": {"sample_id": f"s-{count}", "patch": {"metadata": {"title": "changed"}}},
                }]},
            })
            assert batch.status_code == 200, batch.text
            assert (await client.post("/api/checkpoint")).status_code == 200
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Loading 100,005 trajectory dictionaries would exceed this by many times.
    assert peak < 4_000_000


async def test_json_imports_commit_without_checkpoint_and_preserve_keys_policy(compiler, tmp_path):
    path = tmp_path / "project.sqlite"
    DatasetStore.initialize(path, compiler)
    app = create_app(path)
    config = default_config()
    config["endpoints"][0]["api_key"] = "do-not-persist"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        response = await client.post("/api/import", json={"load_config": True, "files": [
            {"name": "old.kidra-dataset", "text": json.dumps({"samples": [{"id": "a"}], "config": config})},
            {"name": "more.jsonl", "text": '{"id":"b"}\n{"id":"c"}\n'},
        ]})
        assert response.status_code == 200, response.text
        assert response.json()["imported"] == 3
        assert len(DatasetStore(path).samples) == 3
        revision = app.state.dataset["revision"]
        failed = await client.post("/api/import", json={"replace": True, "load_config": True, "files": [{
            "name": "duplicates.json", "text": json.dumps({"samples": [{"id": "x"}, {"id": "x"}], "config": default_config()}),
        }]})
        assert failed.status_code == 422
        assert app.state.dataset["revision"] == revision
        assert len(app.state.dataset["samples"]) == 3
    restarted = create_app(path)
    assert len(restarted.state.dataset["samples"]) == 3
    assert "do-not-persist" not in DatasetStore(path).info()[3]
    assert DatasetStore(path).load_compiler().fingerprint == compiler.fingerprint
    assert path.read_bytes().startswith(b"SQLite format 3\0")


def test_cli_streams_export_and_preserves_output_on_failure(compiler, sample, tmp_path):
    path, output = tmp_path / "project.sqlite", tmp_path / "chunks.jsonl"
    store = DatasetStore.initialize(path, compiler)
    sample["metadata"] = {"validation_level": 3}
    store.replace([sample], 0)
    command = [sys.executable, "-m", "trajectory_remix", "--dataset", str(path), "--export", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text().splitlines()[0])["sample_id"] == sample["id"]
    previous = output.read_bytes()
    store.samples.append({"id": "invalid", "replay": {}, "metadata": {"validation_level": 3}})
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert output.read_bytes() == previous
