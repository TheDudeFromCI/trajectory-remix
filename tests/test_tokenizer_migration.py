import asyncio
import json
import threading

import pytest
from tokenizers import Tokenizer

from trajectory_remix.authoring import CommandError
from trajectory_remix.compiler import Compiler
from trajectory_remix.dataset import new_sample
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore
from trajectory_remix.tools import ToolRegistry


def replacement(compiler):
    config = json.loads(compiler.tokenizer.to_str())
    vocab = config["model"]["vocab"]
    vocab["a"], vocab["b"] = vocab["b"], vocab["a"]
    return config


async def migrate(app, client, tokenizer, **extra):
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": tokenizer, "expected_revision": app.state.dataset["revision"], **extra,
    })
    assert response.status_code == 202, response.text
    await asyncio.wait_for(asyncio.shield(app.state.tokenizer_migration.task), 10)
    return (await client.get("/api/tokenizer/migration")).json()


async def test_migration_recompiles_all_and_activates_every_consumer(studio, monkeypatch):
    app, client = studio
    store = app.state.dataset.store
    store.samples.append(new_sample())
    before = list(store.samples)
    old = (await client.post("/api/compile", json={"sample": before[0]})).json()
    old_compiler = app.state.compiler
    config = replacement(old_compiler)
    expected = Compiler(Tokenizer.from_str(json.dumps(config)), slot_policy=old_compiler.slot_policy)
    calls = []
    original = Compiler.compile

    def observe(self, sample):
        if self.fingerprint == expected.fingerprint:
            calls.append(sample["uuid"])
        return original(self, sample)

    monkeypatch.setattr(Compiler, "compile", observe)
    status = await migrate(app, client, config)
    assert status["status"] == "completed", status
    assert status["converted"] == status["total"] == 2
    assert calls == [s["uuid"] for s in before]
    assert list(store.samples) == before
    assert status["sha256"] != old["tokenizer_sha256"]
    assert app.state.compiler is app.state.commands.compiler is app.state.runner.compiler is app.state.scenario_runner.compiler
    assert app.state.commands.previews.compiler is app.state.compiler
    assert (await client.get(f"/api/frame/{old['plan_id']}/0")).status_code == 404
    assert not app.state.history.undo_stack and not app.state.history.redo_stack
    state = (await client.get("/api/state")).json()
    assert state["tokenizer"]["sha256"] == expected.fingerprint
    assert state["tokenizer_migration"] == status
    plan = (await client.post("/api/compile", json={"sample": before[1]})).json()
    assert plan["tokenizer_sha256"] == expected.fingerprint
    reopened = create_app(store.path)
    assert reopened.state.compiler.fingerprint == expected.fingerprint
    assert reopened.state.compiler.special == old_compiler.special
    for sample in before:
        assert reopened.state.compiler.compile(sample) == expected.compile(sample)


async def test_failed_migration_rolls_back_and_can_retry(studio):
    app, client = studio
    store = app.state.dataset.store
    broken = new_sample()
    broken["id"] = "broken"
    broken["replay"]["1"][0]["content"] = "x{N_TICKS:3}"
    store.samples.append(broken)
    before = list(store.samples)
    revision = store.info()[0]
    original = app.state.compiler
    with store.connect() as db:
        db.execute("INSERT INTO tool_compilations VALUES(?,?,?,?,?)", (before[0]["uuid"], "source", "tools", None, "old-plan"))
    status = await migrate(app, client, replacement(original))
    assert status["status"] == "failed"
    assert status["converted"] == 1 and status["total"] == 2
    assert status["sample_id"] == "broken"
    assert "Invalid marker" in status["diagnostics"][0]["message"]
    assert app.state.compiler is original
    assert store.info()[0] == revision
    assert store.load_compiler().fingerprint == original.fingerprint
    assert list(store.samples) == before
    with store.connect() as db:
        assert db.execute("SELECT plan FROM tool_compilations").fetchall() == [("old-plan",)]
    del store.samples[1]
    assert (await migrate(app, client, replacement(original)))["status"] == "completed"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM tool_compilations").fetchone()[0] == 0


async def test_new_token_boundaries_fail_without_changing_sources(studio):
    app, client = studio
    store = app.state.dataset.store
    sample = store.samples[0]
    sample["replay"] = {
        "0": [{"op": "open", "window": "a"}],
        "1": [{"op": "generate", "window": "a", "content": "a{N_TICK:1}b<|eos|>"}],
    }
    app.state.compiler.compile(sample)
    store.samples[0] = sample
    config = replacement(app.state.compiler)
    config["pre_tokenizer"] = None
    config["model"]["vocab"]["ab"] = len(config["model"]["vocab"])
    config["model"]["merges"] = [["a", "b"]]
    original = app.state.compiler.fingerprint
    status = await migrate(app, client, config)
    assert status["status"] == "failed"
    assert "splits a tokenizer token" in status["error"]
    assert store.samples[0] == sample
    assert store.load_compiler().fingerprint == original


async def test_external_tool_plans_are_replaced_atomically(studio, tmp_path):
    app, client = studio
    folder = tmp_path / "tools"
    folder.mkdir()
    (folder / "custom.py").write_text(
        'name = "custom"\nschema = {"type": "object", "properties": {}, "additionalProperties": False}\n'
        'def run(args, state, windows):\n    return "ab", True, True\n'
    )
    store = app.state.dataset.store
    current, _ = store.configure_tools(ToolRegistry(folder))
    # Use the same registry across the session, as create_app does on startup.
    app.state.compiler.tools = current.tools
    sample = store.samples[0]
    sample["replay"]["1"] = [{"op": "tool-call", "tool": "custom", "arguments": {}, "window": "result"}]
    store.samples[0] = sample
    app.state.compiler.compile(sample)
    with store.connect() as db:
        old = json.loads(db.execute("SELECT plan FROM tool_compilations").fetchone()[0])
        dependencies = db.execute("SELECT * FROM tool_dependencies").fetchall()
    status = await migrate(app, client, replacement(current))
    assert status["status"] == "completed", status
    with store.connect() as db:
        row = db.execute("SELECT source_hash,tools_hash,plan FROM tool_compilations").fetchone()
        assert row[:2] == (store.row(sample)[-1], current.tools.fingerprint)
        plan = json.loads(row[2])
        assert plan["tokenizer_sha256"] != old["tokenizer_sha256"]
        assert plan["tokenizer_sha256"] == app.state.compiler.fingerprint
        assert plan["rows"] != old["rows"]
        assert db.execute("SELECT * FROM tool_dependencies").fetchall() == dependencies
    # A subsequent failed migration must preserve the newly installed plan too.
    broken = new_sample()
    broken["replay"]["1"][0]["content"] = "x{N_TICKS:3}"
    store.samples.append(broken)
    assert (await migrate(app, client, replacement(app.state.compiler)))["status"] == "failed"
    with store.connect() as db:
        assert db.execute("SELECT source_hash,tools_hash,plan FROM tool_compilations").fetchone() == row


@pytest.mark.parametrize("tokenizer,special", [
    ("not json", {}), ({}, {}), (None, {}),
    ("valid", {"bos": "missing"}), ("valid", {"bos": "<|eos|>"}),
    ("valid", {"unknown": "token"}), ("valid", {"eos": 42}),
])
async def test_invalid_configuration_never_starts(studio, tokenizer, special):
    app, client = studio
    if tokenizer == "valid":
        tokenizer = replacement(app.state.compiler)
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": tokenizer, "special": special, "expected_revision": app.state.dataset["revision"],
    })
    assert response.status_code == 422
    assert app.state.tokenizer_migration.task is None


async def test_empty_migration_and_custom_special_tokens(studio):
    app, client = studio
    store = app.state.dataset.store
    store.replace([], store.info()[0])
    status = await migrate(app, client, json.dumps(replacement(app.state.compiler)),
                           special={"bos": "<|eos|>", "eos": "<|bos|>"})
    assert status["status"] == "completed"
    assert status["converted"] == status["total"] == 0
    assert store.load_compiler().special == app.state.compiler.special
    assert app.state.compiler.special["bos"] == "<|eos|>"


@pytest.mark.parametrize("runner_name", ["runner", "scenario_runner"])
async def test_generation_prevents_migration(studio, runner_name):
    app, client = studio
    runner = getattr(app.state, runner_name)
    runner.status["running"] = True
    response = await client.post("/api/tokenizer/migrate", json={
        "tokenizer": replacement(app.state.compiler), "expected_revision": app.state.dataset["revision"],
    })
    assert response.status_code == 409
    runner.status["running"] = False
    assert app.state.tokenizer_migration.task is None


async def test_live_progress_busy_guards_and_revision_conflict(studio, monkeypatch):
    app, client = studio
    store = app.state.dataset.store
    store.samples.append(new_sample())
    before = store.info()[0]
    config = replacement(app.state.compiler)
    reached, release = threading.Event(), threading.Event()
    original = Compiler.compile
    compiled = 0

    def slow(self, sample):
        nonlocal compiled
        compiled += 1
        if compiled == 2:
            reached.set()
            assert release.wait(10)
        return original(self, sample)

    monkeypatch.setattr(Compiler, "compile", slow)
    response = await client.post("/api/tokenizer/migrate", json={"tokenizer": config, "expected_revision": before})
    assert response.status_code == 202
    try:
        assert await asyncio.to_thread(reached.wait, 5)
        status = (await client.get("/api/tokenizer/migration")).json()
        assert status == {"status": "running", "converted": 1, "total": 2}
        assert (await client.get("/api/state")).status_code == 200
        for path in ("/api/samples", "/api/run", "/api/tokenizer/migrate"):
            assert (await client.post(path, json={})).status_code == 409
        with pytest.raises(CommandError, match="migration"):
            await app.state.commands.call("create_trajectory", {})
        with pytest.raises(ValueError, match="migration"):
            app.state.runner.start()
        with pytest.raises(ValueError, match="migration"):
            app.state.scenario_runner.start("anything")
        # Simulate a different server writing during compilation; readers and
        # writers stay responsive and the final install must reject this change.
        store.samples.append(new_sample())
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(app.state.tokenizer_migration.task), 10)
    status = (await client.get("/api/tokenizer/migration")).json()
    assert status["status"] == "failed" and "Dataset changed" in status["error"]
    assert store.load_compiler().fingerprint == app.state.compiler.fingerprint
    assert store.info()[0] == before + 1
    response = await client.post("/api/tokenizer/migrate", json={"tokenizer": config, "expected_revision": before})
    assert response.status_code == 409
