from types import SimpleNamespace

import pytest

from trajectory_remix.compiler import CompileError
from trajectory_remix.storage import DatasetStore
from trajectory_remix.tools import ExecutionContext, ToolError, ToolRegistry


def visible_call(name="time_advance"):
    return {
        "op": "tool-call",
        "tool": name,
        "arguments": {"seconds": 5},
        "window": "tool",
        "content": "x",
    }


def test_public_schemas_exclude_invisible_tools(compiler):
    assert "time_advance" not in {
        s["function"]["name"] for s in compiler.tools.schemas()
    }
    author = next(
        s
        for s in compiler.tools.schemas(invisible=True)
        if s["function"]["name"] == "time_advance"
    )
    assert author["invisible"] is True
    with pytest.raises(ToolError, match="invisible-only"):
        compiler.tools.get("time_advance").definition()
    with pytest.raises(ToolError, match="invisible-only"):
        compiler.tools.run_tick(
            "time_advance", {"seconds": 5}, {}, [], ExecutionContext()
        )


def test_only_invisible_calls_can_execute_preparation_tools(compiler, sample):
    sample["replay"]["1"] = [visible_call()]
    with pytest.raises(CompileError, match="invisible-only") as error:
        compiler.compile(sample)
    assert error.value.path == "replay[1][0]"
    sample["replay"]["1"] = [
        {
            "op": "invisible-tool-call",
            "tool": "time_advance",
            "arguments": {"seconds": 5},
        }
    ]
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 1)["sandbox"]["clock"] == "2030-01-01T09:00:05+00:00"
    assert compiler.frame(plan, 1)["sandbox"]["calls"] == 0
    assert all(row["event"] != "replay[1][0]" for row in plan["rows"])


def test_show_tools_rejects_explicit_hidden_tools_and_never_randomizes_them(
    compiler, sample, monkeypatch
):
    sample["replay"]["1"] = [
        {"op": "show_tools", "window": "a", "tools": ["time_advance"]}
    ]
    with pytest.raises(CompileError, match="invisible-only"):
        compiler.compile(sample)
    monkeypatch.setattr(
        "trajectory_remix.compiler.random.Random",
        lambda seed: SimpleNamespace(random=lambda: 0.0),
    )
    sample["replay"]["1"][0].update(
        tools=["chat"], **{"randomly-add-additional-tools": True}
    )
    plan = compiler.compile(sample)
    shown = next(
        event["text"] for event in plan["events"] if event["path"] == "replay[1][0]"
    )
    assert '"time_advance"' not in shown
    assert '"chat"' in shown and '"file_read"' in shown


async def test_mcp_blocks_visible_usage_even_in_unvalidated_drafts(studio):
    client = studio[1]
    for command, arguments in [
        ("create_event", {"sample_id": "test", "tick": "1", "event": visible_call()}),
        (
            "create_tool_call",
            {
                "sample_id": "test",
                "tick": "1",
                "tool": "time_advance",
                "arguments": {"seconds": 5},
            },
        ),
    ]:
        response = await client.post(
            "/api/mcp/call", json={"name": command, "arguments": arguments}
        )
        assert response.status_code == 422 and "invisible-only" in response.text
    assert studio[0].state.dataset["revision"] == 0
    await client.post(
        "/api/mcp/call",
        json={"name": "create_tool_session", "arguments": {"session_id": "tools"}},
    )
    response = await client.post(
        "/api/mcp/call",
        json={
            "name": "call_simulated_tool",
            "arguments": {
                "session_id": "tools",
                "name": "time_advance",
                "arguments": {"seconds": 5},
            },
        },
    )
    assert response.status_code == 422 and "invisible-only" in response.text
    response = await client.post(
        "/api/mcp/call",
        json={
            "name": "create_tool_call",
            "arguments": {
                "sample_id": "test",
                "tick": "1",
                "tool": "time_advance",
                "arguments": {"seconds": 5},
                "invisible": True,
            },
        },
    )
    assert response.status_code == 200, response.text


def test_custom_tool_visibility_changes_recompile_dependents(
    compiler, sample, tmp_path
):
    folder = tmp_path / "tools"
    folder.mkdir()
    path = folder / "prepare.py"
    source = """name = "prepare"
invisible = FLAG
schema = {"type": "object"}
def run(args, state, windows):
    state["prepared"] = True
    return "prepared", True, True
"""
    path.write_text(source.replace("FLAG", "False"))
    store = DatasetStore.initialize(tmp_path / "project.sqlite", compiler)
    store.configure_tools(ToolRegistry(folder))
    sample["replay"]["1"] = [{**visible_call("prepare"), "arguments": {}}]
    store.replace([sample], 0)
    assert store.configure_tools(ToolRegistry(folder))[1] == []
    path.write_text(source.replace("FLAG", "True"))
    registry = ToolRegistry(folder)
    compiled, errors = store.configure_tools(registry)
    assert "invisible-only" in errors[0]["message"]
    assert "prepare" not in {s["function"]["name"] for s in registry.schemas()}
    state = {}
    assert registry.run_tick(
        "prepare", {}, state, [], ExecutionContext(invisible=True)
    ) == ("prepared", True, True)
    assert state == {"prepared": True}
    path.write_text(source.replace("FLAG", '"true"'))
    with pytest.raises(ToolError, match="invisible must be a boolean"):
        ToolRegistry(folder)
