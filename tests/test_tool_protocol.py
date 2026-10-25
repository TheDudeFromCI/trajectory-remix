"""Contract tests for opaque state, tick execution, schemas, and custom tool lifecycles."""

import copy
import json
from pathlib import Path
from types import ModuleType

import pytest

from trajectory_remix.compiler import CompileError, visible_to
from trajectory_remix.storage import DatasetStore
from trajectory_remix.tools import ToolRegistry, WindowProperties


def register(registry, name, run, properties=None):
    module = ModuleType(name)
    module.name, module.run = name, run
    module.schema = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    registry.register(module)


def call(name, window="result", args=None):
    return {
        "op": "tool-call",
        "tool": name,
        "arguments": args or {},
        "window": window,
        "content": "x",
    }


def test_opaque_state_sparse_cache_and_recompile(compiler, sample):
    registry = ToolRegistry()
    executions = []

    def run(args, state, windows):
        executions.append(copy.deepcopy(state))
        state.setdefault("arbitrary", {"items": []})["items"].append(len(executions))
        return "updated", True, True

    register(registry, "custom", run)
    register(
        registry, "read_only", lambda args, state, windows: (str(state), True, False)
    )
    compiler.tools = registry
    sample["replay"].update(
        {
            "1": [call("custom")],
            "9": [call("read_only", "reader")],
            "15": [call("custom", "later")],
        }
    )
    plan = compiler.compile(sample)
    assert executions == [{"tick_rate": 10}, {"tick_rate": 10, "arbitrary": {"items": [1]}}]
    assert list(plan["sandbox"]["snapshots"]) == ["2", "16"]
    assert compiler.frame(plan, 1)["sandbox"] == {"tick_rate": 10}
    first = compiler.frame(plan, 2)
    assert first["sandbox"] == {"tick_rate": 10, "arbitrary": {"items": [1]}}
    first["sandbox"]["arbitrary"]["items"].append(99)
    assert compiler.frame(plan, 15)["sandbox"] == {"tick_rate": 10, "arbitrary": {"items": [1]}}
    assert len(executions) == 2  # Scrubbing never reruns tools.
    compiler.compile(sample)
    assert executions[2] == {"tick_rate": 10}  # Recompilation starts with only the configured tick rate.


def test_long_running_tools_run_each_tick_and_expose_only_current_output(
    compiler, sample
):
    registry = ToolRegistry()
    ticks = []

    def run(args, state, windows):
        from trajectory_remix.tools import execution_context

        ticks.append(execution_context.get().tick)
        count = state.get("progress", 0) + 1
        state["progress"] = count
        return f"part {count}", count == 3, True

    register(registry, "long", run)
    compiler.tools = registry
    sample["replay"]["1"] = [{**call("long"), "result-anchor": 4}]
    sample["replay"]["{N_TICK:4}+1"] = [
        {"op": "generate", "window": "a", "content": "done"}
    ]
    plan = compiler.compile(sample)
    assert ticks == [2, 3, 4]
    assert plan["anchors"]["4"]["tick"] == 4
    early = compiler.frame(plan, 2)
    assert early["sandbox"] == {"tick_rate": 10, "progress": 1}
    assert early["tool_calls"][0]["status"] == "running"
    assert "part 1" in early["windows"]["result"]["text"]
    assert "part 2" not in early["windows"]["result"]["text"]
    assert "outcome" not in early["tool_calls"][0]
    assert compiler.frame(plan, 4)["tool_calls"][0]["status"] == "completed"


def test_false_modified_flag_is_trusted_without_comparing_state(compiler, sample):
    registry = ToolRegistry()

    class NoComparison(dict):
        def __eq__(self, other):
            pytest.fail("The scheduler must not compare sandbox dictionaries")

    def write(args, state, windows):
        state["custom"] = NoComparison(n=1)
        return "", True, True

    register(registry, "write", write)
    register(registry, "read", lambda args, state, windows: ("read", True, False))
    compiler.tools = registry
    sample["replay"].update({"1": [call("write")], "9": [call("read", "read")]})
    plan = compiler.compile(sample)
    assert len(plan["sandbox"]["snapshots"]) == 1


def test_tools_can_append_replace_and_inspect_windows(compiler, sample):
    registry = ToolRegistry()
    seen = []

    def writer(args, state, windows):
        window = next(w for w in windows if w.name == "a")
        seen.append((window.purpose, window.content))
        window.content = "replacement"
        return "written", True, False

    register(registry, "writer", writer)
    compiler.tools = registry
    sample["replay"].update(
        {
            "1": [
                {"op": "append", "window": "a", "source": "user", "content": "original"}
            ],
            "2": [call("writer")],
            "4": [{"op": "generate", "window": "b", "content": "x"}],
        }
    )
    plan = compiler.compile(sample)
    assert seen == [("document", "original")]
    assert compiler.frame(plan, 2)["windows"]["a"]["text"].endswith("original")
    final = compiler.frame(plan, 4)["windows"]["a"]["text"]
    assert final.endswith("replacement") and "original" not in final
    query = next(r for r in plan["rows"] if r["window"] == "b" and r["tick"] == 4)
    removed = [
        r for r in plan["rows"] if r["window"] == "a" and r["event"] == "replay[1][0]"
    ]
    assert not any(visible_to(query, row, plan["windows"]) for row in removed)
    assert plan["sandbox"]["snapshots"] == {}
    window = WindowProperties("a", "document", "text")
    with pytest.raises(AttributeError):
        window.name = "b"
    with pytest.raises(ValueError, match="immutable"):
        window["purpose"] = "other"


def test_invisible_chat_username_and_public_schema(compiler, sample):
    assert (
        "username"
        not in compiler.tools.get("chat").definition()["function"]["parameters"][
            "properties"
        ]
    )
    sample["replay"]["0"].append(
        {"op": "open", "window": "chat", "window-type": "chat"}
    )
    sample["replay"]["1"] = [
        {
            "op": "invisible-tool-call",
            "tool": "chat",
            "arguments": {"content": "Hello", "username": "Alex"},
        }
    ]
    plan = compiler.compile(sample)
    assert "Alex: Hello" in compiler.frame(plan, 1)["windows"]["chat"]["text"]
    assert plan["tool_calls"][0]["invisible"]
    sample["replay"]["1"] = [
        call("chat", args={"content": "Hello", "username": "Alex"})
    ]
    with pytest.raises(CompileError, match="Invisible argument"):
        compiler.compile(sample)


def test_show_tools_is_literal_masked_and_deterministic(compiler, sample):
    sample["replay"]["1"] = [{"op": "show_tools", "window": "a", "tools": ["chat"]}]
    plan = compiler.compile(sample)
    text = compiler.frame(plan, plan["max_tick"])["windows"]["a"]["text"]
    assert '"chat"' in text and '"username"' not in text and '"file_read"' not in text
    assert all(
        r["loss_mask"] == 0 for r in plan["rows"] if r["event"] == "replay[1][0]"
    )
    assert sample["replay"]["1"][0]["op"] == "show_tools"
    sample["replay"]["1"][0]["randomly-add-additional-tools"] = True
    assert compiler.compile(sample) == compiler.compile(sample)
    sample["replay"]["1"][0]["tools"] = ["missing"]
    with pytest.raises(CompileError, match="missing"):
        compiler.compile(sample)


@pytest.mark.parametrize(
    "listing", ["missing", "same_tick", "late", "closed", "replaced", "wrong_tool"]
)
def test_tool_listing_warning_respects_visible_context(compiler, sample, listing):
    sample["replay"]["3"] = [call("calculator", args={"expression": "2+2"})]
    if listing != "missing":
        at = "3" if listing == "same_tick" else "4" if listing == "late" else "1"
        sample["replay"].setdefault(at, []).append(
            {
                "op": "show_tools",
                "window": "a",
                "tools": ["file_list" if listing == "wrong_tool" else "calculator"],
            }
        )
    if listing == "closed":
        sample["replay"]["2"] = [
            {"op": "generate", "window": "a", "content": "<|eos|>"}
        ]
    elif listing == "replaced":
        sample["replay"]["2"] = [
            {
                "op": "append",
                "window": "a",
                "source": "system",
                "content": "Replacement context",
                "replace-content": True,
            }
        ]
    plan = compiler.compile(sample)
    warnings = [d for d in plan["diagnostics"] if d.get("code") == "tool_not_shown"]
    assert bool(warnings) == (listing != "same_tick")
    if warnings:
        assert warnings[0]["severity"] == "warning"
        assert warnings[0]["path"] == "replay[3][0]"
        assert "calculator" in warnings[0]["message"]


def test_randomized_tool_listing_uses_actual_expansion(compiler, sample):
    sample["replay"]["1"] = [
        {
            "op": "show_tools",
            "window": "a",
            "tools": [],
            "randomly-add-additional-tools": True,
        }
    ]
    expanded = compiler.show_tools(sample["replay"]["1"][0], sample, "replay[1][0]")
    listed = {s["function"]["name"] for s in json.loads(expanded["content"])}
    sample["replay"]["3"] = [call("calculator", args={"expression": "2+2"})]
    warnings = [
        d
        for d in compiler.compile(sample)["diagnostics"]
        if d.get("code") == "tool_not_shown"
    ]
    assert bool(warnings) == ("calculator" not in listed)


def test_invisible_setup_does_not_require_tool_listing(compiler, sample):
    sample["replay"]["1"] = [
        {
            "op": "invisible-tool-call",
            "tool": "file_write",
            "arguments": {"path": "fixture.txt", "content": "authored input"},
        }
    ]
    assert not any(
        d.get("code") == "tool_not_shown"
        for d in compiler.compile(sample)["diagnostics"]
    )


def external_file(folder, text="first", schema=None):
    schema = schema or {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    path = folder / "custom.py"
    path.write_text(
        f'name = "custom"\nschema = {schema!r}\ndef run(args, state, windows):\n    state["arbitrary"] = {text!r}\n    return {text!r}, True, True\n'
    )
    return path


def test_external_files_track_schema_hash_and_recompile(compiler, sample, tmp_path):
    folder = tmp_path / "tools"
    folder.mkdir()
    path = external_file(folder)
    store = DatasetStore.initialize(tmp_path / "dataset.sqlite", compiler)
    store.configure_tools(ToolRegistry(folder))
    sample["replay"]["1"] = [call("custom")]
    store.replace([sample], 0)
    configured, diagnostics = store.configure_tools(ToolRegistry(folder))
    assert diagnostics == []
    saved = store.samples[0]
    with store.connect() as db:
        first_hash = db.execute("SELECT file_hash FROM external_tools").fetchone()[0]
        assert json.loads(
            db.execute("SELECT plan FROM tool_compilations").fetchone()[0]
        )["sandbox"]["snapshots"]["2"] == {"tick_rate": 10, "arbitrary": "first"}
    external_file(
        folder, "other"
    )  # Same size edit must bypass Python's bytecode cache.
    configured, diagnostics = store.configure_tools(ToolRegistry(folder))
    assert diagnostics == []
    with store.connect() as db:
        assert (
            db.execute("SELECT file_hash FROM external_tools").fetchone()[0]
            != first_hash
        )
        assert "other" in db.execute("SELECT plan FROM tool_compilations").fetchone()[0]
    external_file(
        folder,
        schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    configured, diagnostics = store.configure_tools(ToolRegistry(folder))
    assert "schema changed" in diagnostics[0]["message"]
    with pytest.raises(CompileError, match="schema changed"):
        configured.compile(saved)
    path.unlink()
    configured, diagnostics = store.configure_tools(ToolRegistry(folder))
    assert "missing" in diagnostics[0]["message"]
    with pytest.raises(CompileError, match="missing"):
        configured.compile(saved)


def test_external_loader_rejects_duplicates_and_malformed_modules(tmp_path):
    (tmp_path / "bad.py").write_text(
        'name = "chat"\nschema = {"type":"object"}\ndef run(args,state,windows): return "", True, False\n'
    )
    with pytest.raises(ValueError, match="Duplicate tool"):
        ToolRegistry(tmp_path)
    (tmp_path / "bad.py").write_text('name = "bad"\nschema = {"type":"object"}\n')
    with pytest.raises(ValueError, match="run must"):
        ToolRegistry(tmp_path)


async def test_mcp_rejects_invisible_args_even_without_compile(studio):
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "create_event",
            "arguments": {
                "sample_id": "test",
                "tick": "1",
                "event": call("chat", args={"content": "hello", "username": "spoof"}),
            },
        },
    )
    assert response.status_code == 422 and "Invisible argument" in response.text
    assert studio[0].state.dataset["revision"] == 0


def test_streamed_outputs_queue_without_interleaving(compiler, sample):
    registry = ToolRegistry()

    def run(args, state, windows):
        n = state.get("count", 0) + 1
        state["count"] = n
        return str(n), n == 3, True

    register(registry, "progress", run)
    compiler.tools = registry
    sample["replay"]["1"] = [
        {**call("progress"), "delivery": "stream", "result-anchor": 1}
    ]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["end"] == 4
    assert plan["anchors"]["1"]["tick"] > 4
    text = compiler.frame(plan, plan["max_tick"])["windows"]["result"]["text"]
    assert text.endswith("".join(f"<tool_result>{n}</tool_result>" for n in (1, 2, 3)))


def test_local_schema_refs_hide_invisible_args_and_preserve_named_properties():
    from trajectory_remix.tools import public_schema, ToolError

    registry = ToolRegistry()
    module = ModuleType("nested")
    module.name = "nested"
    module.run = lambda args, state, windows: ("done", True, False)
    module.schema = {
        "type": "object",
        "properties": {
            "invisible": {"type": "boolean"},
            "nested": {"$ref": "#/$defs/body"},
        },
        "$defs": {
            "body": {
                "type": "object",
                "properties": {
                    "secret": {"type": "string", "invisible": True},
                    "public": {"type": "string"},
                },
                "additionalProperties": True,
            }
        },
    }
    registry.register(module)
    public = public_schema(module.schema)
    assert "invisible" in public["properties"]
    assert "secret" not in public["$defs"]["body"]["properties"]
    with pytest.raises(ToolError, match="Invisible argument arguments.nested.secret"):
        registry.validate("nested", {"nested": {"secret": "hidden"}})
    registry.validate("nested", {"nested": {"secret": "allowed"}}, invisible=True)


def test_custom_modules_can_use_dataclasses_and_future_annotations(tmp_path):
    (tmp_path / "dataclass_tool.py").write_text("""from __future__ import annotations
from dataclasses import dataclass
@dataclass
class Value:
    content: str
name = "dataclass_tool"
schema = {"type": "object"}
def run(args, state, windows):
    return Value("ok").content, True, False
""")
    assert ToolRegistry(tmp_path).get("dataclass_tool").run({}, {}, []) == (
        "ok",
        True,
        False,
    )


def test_show_tools_dependencies_fail_after_custom_tool_removal(
    compiler, sample, tmp_path
):
    folder = tmp_path / "tools"
    folder.mkdir()
    path = external_file(folder)
    store = DatasetStore.initialize(tmp_path / "project.sqlite", compiler)
    store.configure_tools(ToolRegistry(folder))
    sample["replay"]["1"] = [
        {
            "op": "show_tools",
            "window": "a",
            "tools": [],
            "randomly-add-additional-tools": True,
        }
    ]
    store.replace([sample], 0)
    assert store.configure_tools(ToolRegistry(folder))[1] == []
    path.unlink()
    _, errors = store.configure_tools(ToolRegistry(folder))
    assert "missing" in errors[0]["message"]
    assert "missing" in store.configure_tools(ToolRegistry(folder))[1][0]["message"]


def test_previous_database_schema_migrates_without_changing_trajectories(
    compiler, sample, tmp_path
):
    path = tmp_path / "old.sqlite"
    store = DatasetStore.initialize(path, compiler)
    store.replace([sample], 0)
    saved = store.samples[0]
    with store.connect() as db:
        for table in ("tool_dependencies", "tool_compilations", "external_tools"):
            db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE project SET version=3")
    migrated = DatasetStore(path)
    assert migrated.samples[0] == saved
    assert migrated.info()[:3] == store.info()[:3]
    with migrated.connect() as db:
        assert db.execute("SELECT version FROM project").fetchone()[0] == 4


async def test_workbench_records_generic_window_writes(studio):
    registry = studio[0].state.compiler.tools
    # Keep the application's shared built-in registry unchanged after this test.
    registry = copy.copy(registry)
    registry.tools = dict(registry.tools)

    def run(args, state, windows):
        window = next(w for w in windows if w.name == "a")
        window.content = "replacement"
        return "written", True, False

    register(registry, "writer", run)
    studio[0].state.tool_sessions.registry = registry

    async def command(command_name, **arguments):
        response = await studio[1].post(
            "/api/mcp/call", json={"name": command_name, "arguments": arguments}
        )
        assert response.status_code == 200, response.text
        return response.json()

    await command("create_tool_session", session_id="custom")
    result = await command(
        "call_simulated_tool",
        session_id="custom",
        name="writer",
        arguments={},
        record_sample_id="test",
    )
    assert result["interaction"]["outcome"]["ok"]
    plan = studio[0].state.compiler.compile(studio[0].state.dataset["samples"][0])
    assert (
        studio[0]
        .state.compiler.frame(plan, plan["max_tick"])["windows"]["a"]["text"]
        .endswith("replacement")
    )
