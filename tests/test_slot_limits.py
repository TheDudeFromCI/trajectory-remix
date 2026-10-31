import json
import copy
import uuid

import httpx
import pytest

from trajectory_remix.__main__ import main
from trajectory_remix.compiler import CompileError, Compiler, chunks
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore


def source(ident, count=3):
    return {
        "schema-version": "1.0", "tick-policy": "external-first", "id": "label", "uuid": ident,
        "replay": {"0": [{"op": "open", "window": f"w{i}"} for i in range(count)]},
    }


def test_uuid_seed_is_stable_unique_and_shared_by_headers_references_and_exports(compiler):
    compiler = Compiler(compiler.tokenizer, max_window_slots=32)
    sample = source("00000000-0000-4000-8000-000000000001")
    sample["replay"]["1"] = [{"op": "generate", "window": "w0", "content": "{WINDOW:w2}"}]
    original = copy.deepcopy(sample)
    plan = compiler.compile(sample)
    slots = plan["window_slots"]
    assert len(set(slots.values())) == 3 and all(0 <= slot < 32 for slot in slots.values())
    assert compiler.compile(sample)["window_slots"] == slots
    sample["id"] = "renamed-label"
    sample["metadata"] = {"tags": ["edited"]}
    assert compiler.compile(sample)["window_slots"] == slots
    fresh = Compiler(compiler.tokenizer, max_window_slots=32)
    assert fresh.compile(original)["window_slots"] == slots
    assert next(e for e in plan["events"] if e["op"] == "generate")["text"] == str(slots["w2"])
    assert next(w for w in plan["windows"] if w["id"] == "w2")["header"].startswith(f"<|cxt|>Window: {slots['w2']}\n")
    exported = list(chunks(plan, 4))
    assert all(c["trajectory_uuid"] == original["uuid"] and c["window_slots"] == slots for c in exported)
    other = source("00000000-0000-4000-8000-000000000002")
    assert fresh.slots_for(other) != slots
    extended = source(original["uuid"], 4)
    assert {k: v for k, v in fresh.slots_for(extended).items() if k in slots} == slots


def test_slot_limit_counts_implicit_windows_and_allows_reusing_same_window(compiler):
    compiler = Compiler(compiler.tokenizer, max_window_slots=2)
    sample = source(str(uuid.uuid4()), 2)
    sample["replay"]["1"] = [{"op": "tool-call", "window": "tool", "tool": "file_list", "arguments": {}}]
    with pytest.raises(CompileError, match="3 window slots; project limit is 2"):
        compiler.compile(sample)
    sample["replay"]["1"][0]["window"] = "w1"
    sample["replay"]["0"][1]["window-type"] = "tool"
    assert len(compiler.compile(sample)["window_slots"]) == 2


async def test_limits_are_persisted_and_enforced_for_crud_imports_and_mcp(tmp_path, compiler):
    path = tmp_path / "project.sqlite"
    compiler = Compiler(compiler.tokenizer, max_window_slots=2)
    store = DatasetStore.initialize(path, compiler)
    sample = source(str(uuid.uuid4()), 2)
    store.samples.append(sample)
    assert DatasetStore(path).load_compiler().max_window_slots == 2
    assert DatasetStore(path).load_compiler().slots_for(sample) == compiler.slots_for(sample)
    app = create_app(path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver", headers={"X-Remix-Token": app.state.csrf}) as client:
        oversized = source(str(uuid.uuid4()), 3)
        assert (await client.post("/api/samples", json={"sample": oversized})).status_code == 422
        response = await client.post("/api/import", json={"files": [{"name": "large.json", "text": __import__("json").dumps(oversized)}]})
        assert response.status_code == 422
        response = await client.post("/api/mcp/call", json={"name": "create_event", "arguments": {
            "sample_id": "label", "tick": "0", "event": {"op": "open", "window": "third"},
        }})
        assert response.status_code == 422, response.text
        assert len(store.samples) == 1 and store.samples[0] == sample
        assert store.info()[0] == 1


async def test_generation_cannot_accept_a_trajectory_above_the_project_limit(tmp_path, compiler, monkeypatch):
    from test_idle_scenario import IdleClient, setup_idle
    compiler = Compiler(compiler.tokenizer, max_window_slots=1)
    app, runner = setup_idle(tmp_path, compiler, monkeypatch, IdleClient())
    runner.start('idle_scenario')
    await runner.job
    assert runner.status['accepted'] == 0
    assert len(app.state.dataset['samples']) == 1  # Keep last compilable checkpoint.
    sample = app.state.dataset['samples'][0]
    assert sample['metadata']['validation_level'] == 0
    assert 'project limit is 1' in json.dumps(sample['metadata']['generation_record']['failures'])
    compiler.compile(sample)


def test_init_slot_limit_is_fixed_for_project(tmp_path):
    path = tmp_path / "project.sqlite"
    main(["init", "--dataset", str(path), "--tokenizer", "examples/demo-tokenizer.json", "--max-window-slots", "8"])
    assert DatasetStore(path).load_compiler().max_window_slots == 8
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(path), "--max-window-slots", "16"])
    assert error.value.code == 2
    invalid = tmp_path / "invalid.sqlite"
    with pytest.raises(SystemExit):
        main(["init", "--dataset", str(invalid), "--tokenizer", "examples/demo-tokenizer.json", "--max-window-slots", "0"])
    assert not invalid.exists()
