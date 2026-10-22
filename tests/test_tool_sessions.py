import asyncio
import copy
import json

import pytest

from trajectory_remix.authoring import CommandError
from trajectory_remix.tool_sessions import ToolSessionError, ToolSessions


async def call(studio, command, **arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": command, "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def tool(studio, name, arguments, session_id="tools", **options):
    return await call(
        studio,
        "call_simulated_tool",
        session_id=session_id,
        name=name,
        arguments=arguments,
        **options,
    )


async def test_chat_session_uses_recording_target_and_is_undoable(studio, compiler):
    await call(studio, "create_tool_session", session_id="tools")
    standalone = await tool(studio, "chat", {"content": "Hello"})
    assert not standalone["interaction"]["outcome"]["ok"]
    sample = studio[0].state.dataset["samples"][0]
    sample["replay"]["0"].append({"op": "open", "window": "chat"})
    sample["metadata"] = {"agent_name": "Nova"}
    before = copy.deepcopy(sample)
    recorded = await tool(studio, "chat", {"content": "Hello {N_TICK:9}"}, record_sample_id="test")
    assert recorded["interaction"]["outcome"]["ok"]
    current = (await call(studio, "get_trajectory", sample_id="test"))["sample"]
    plan = compiler.compile(current)
    assert plan["anchors"] == {}
    assert compiler.frame(plan, plan["max_tick"])["windows"]["chat"]["text"] == (
        "<|cxt|>Window: 2\nPurpose: document<|/cxt|>"
        "<|msg|>Nova: Hello {N_TICK:9}<|/msg|>\n"
    )
    await call(studio, "undo_edits")
    assert studio[0].state.dataset["samples"][0] == before
    await call(studio, "redo_edits")
    assert studio[0].state.dataset["samples"][0] == current


async def test_simulated_tools_use_real_dispatcher_and_are_isolated(studio, tmp_path):
    await call(
        studio,
        "create_tool_session",
        session_id="tools",
        clock_seed="2031-01-01T00:00:00Z",
        seconds_per_call=2,
    )
    await call(studio, "create_tool_session", session_id="other")
    definitions = (await call(studio, "list_simulated_tools", session_id="tools"))[
        "tools"
    ]
    assert len(definitions) == 16
    assert not next(d for d in definitions if d["function"]["name"] == "subagent")[
        "available"
    ]
    file_path = str(tmp_path / "must-stay-virtual.txt")
    wrote = await tool(
        studio, "file_write", {"path": file_path, "content": "in RAM only"}
    )
    assert wrote["interaction"]["outcome"] == {
        "ok": True,
        "result": {"saved": file_path},
    }
    assert not (tmp_path / "must-stay-virtual.txt").exists()
    assert (await tool(studio, "file_read", {"path": file_path}))["interaction"][
        "outcome"
    ]["result"]["content"] == "in RAM only"
    assert (await tool(studio, "file_list", {}, session_id="other"))["interaction"][
        "outcome"
    ]["result"] == []
    await tool(studio, "memory_save", {"key": "one", "text": "Remix tools"})
    memory = await tool(studio, "memory_search", {"query": "Remix"})
    assert memory["interaction"]["outcome"]["result"][0]["key"] == "one"
    await tool(
        studio, "note_save", {"key": "work", "text": "inspect", "status": "todo"}
    )
    await tool(studio, "skill_save", {"name": "audit", "content": "Verify results"})
    assert (await tool(studio, "note_list", {}))["interaction"]["outcome"]["result"][
        "work"
    ]["status"] == "todo"
    assert (await tool(studio, "skill_load", {"name": "audit"}))["interaction"][
        "outcome"
    ]["result"]["content"] == "Verify results"
    assert (await tool(studio, "skill_list", {}))["interaction"]["outcome"][
        "result"
    ] == ["audit"]
    await tool(studio, "file_delete", {"path": file_path})
    await tool(studio, "note_delete", {"key": "work"})
    clock = await tool(studio, "time_advance", {"seconds": 10})
    assert clock["interaction"]["outcome"]["result"]["synthetic"]
    state = await call(studio, "get_tool_session", session_id="tools")
    assert state["calls"] == 12 and state["clock"] == "2031-01-01T00:00:34+00:00"
    assert (
        state["state"]["files"] == {}
        and state["state"]["memory"]["one"] == "Remix tools"
    )
    assert len((await call(studio, "list_tool_sessions"))["sessions"]) == 2
    assert studio[0].state.dataset["revision"] == 0
    assert not (await call(studio, "get_edit_history"))["can_undo"]
    await call(studio, "save_dataset")
    assert "in RAM only" not in (tmp_path / "project.kidra-dataset").read_text()
    old_sandbox = studio[0].state.tool_sessions.get("tools").sandbox
    reset = await call(studio, "reset_tool_session", session_id="tools")
    assert (
        old_sandbox.closed and reset["calls"] == 0 and reset["interaction_count"] == 0
    )
    assert reset["clock"] == "2031-01-01T00:00:00+00:00"
    await call(studio, "delete_tool_session", session_id="tools")
    assert len((await call(studio, "list_tool_sessions"))["sessions"]) == 1


async def test_failures_and_quotas_preserve_dispatcher_semantics(studio):
    await call(
        studio, "create_tool_session", session_id="tools", max_calls=3, max_bytes=128
    )
    before = await call(studio, "get_tool_session", session_id="tools")
    for name, arguments in [("shell", {"command": "id"}), ("file_list", {"path": "/"})]:
        response = await studio[1].post(
            "/api/mcp/call",
            json={
                "name": "call_simulated_tool",
                "arguments": {
                    "session_id": "tools",
                    "name": name,
                    "arguments": arguments,
                },
            },
        )
        assert response.status_code == 422
        assert await call(studio, "get_tool_session", session_id="tools") == before
    failed = await tool(studio, "file_read", {"path": "missing"})
    assert (
        not failed["interaction"]["outcome"]["ok"]
        and failed["interaction"]["call_number"] == 1
    )
    await tool(studio, "file_write", {"path": "small", "content": "x" * 60})
    too_large = await tool(studio, "memory_save", {"key": "memory", "text": "x" * 60})
    assert not too_large["interaction"]["outcome"]["ok"]
    assert (await call(studio, "get_tool_session", session_id="tools"))["calls"] == 3
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "call_simulated_tool",
            "arguments": {
                "session_id": "tools",
                "name": "time_now",
                "arguments": {},
            },
        },
    )
    assert response.json()["code"] == "quota_exceeded"


async def test_recording_actual_results_is_valid_atomic_and_undoable(studio):
    await call(studio, "create_tool_session", session_id="tools")
    before = copy.deepcopy(studio[0].state.dataset["samples"])
    result = await tool(
        studio,
        "file_write",
        {"path": "/report.txt", "content": "actual result"},
        record_sample_id="test",
        expected_revision=0,
    )
    recording, entry = result["recording"], result["interaction"]
    assert recording["revision"] == 1 and recording["start_tick"] == 1
    sample = (await call(studio, "get_trajectory", sample_id="test"))["sample"]
    events = [e for group in sample["replay"].values() for e in group]
    generated = next(
        e
        for e in events
        if e["window"] == recording["call_window"] and e["op"] == "generate"
    )
    assert recording["call_window"] == recording["result_window"]
    actual_call = json.loads(generated["content"].removeprefix("<tool>").removesuffix("</tool>"))
    assert actual_call == {"tool": entry["name"], "args": entry["arguments"]}
    opener = next(e for e in sample["replay"]["0"] if e["window"] == recording["call_window"])
    assert opener == {"op": "open", "window": recording["call_window"], "window-type": "tool"}
    appended = next(
        e
        for e in events
        if e["window"] == recording["result_window"] and e["op"] == "append"
    )
    assert (
        appended["loss"] == {"mask": "exclude"}
        and json.loads(appended["content"].removeprefix("<tool_result>").removesuffix("</tool_result>")) == entry["outcome"]
    )
    assert appended["request-id"] == generated["request-id"] == recording["request_id"]
    assert (await call(studio, "validate_trajectory", sample_id="test"))["valid"]
    await call(studio, "undo_edits")
    assert studio[0].state.dataset["samples"] == before
    assert (await call(studio, "get_tool_session", session_id="tools"))["state"][
        "files"
    ]["/report.txt"] == "actual result"
    rerecorded = await call(
        studio,
        "record_tool_interaction",
        sample_id="test",
        session_id="tools",
        interaction_id=entry["id"],
    )
    assert rerecorded["request_id"] == recording["request_id"]
    assert (await call(studio, "get_tool_session", session_id="tools"))["calls"] == 1
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "record_tool_interaction",
            "arguments": {
                "sample_id": "test",
                "session_id": "tools",
                "interaction_id": entry["id"],
            },
        },
    )
    assert response.status_code == 409


async def test_recording_on_empty_draft_and_failure_result(studio):
    await call(studio, "create_trajectory", sample_id="empty")
    await call(studio, "create_tool_session", session_id="tools")
    result = await tool(
        studio, "file_read", {"path": "missing"}, record_sample_id="empty"
    )
    assert not result["interaction"]["outcome"]["ok"]
    assert result["recording"]["start_tick"] == 0
    assert (await call(studio, "validate_trajectory", sample_id="empty"))["valid"]


async def test_rejected_recording_rolls_back_ram_clock_log_and_source(studio):
    await call(studio, "create_tool_session", session_id="tools")
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="2",
        event={"op": "generate", "window": "missing", "content": "invalid"},
    )
    state = await call(studio, "get_tool_session", session_id="tools")
    source = copy.deepcopy(studio[0].state.dataset)
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "call_simulated_tool",
            "arguments": {
                "session_id": "tools",
                "name": "file_write",
                "arguments": {"path": "/undo.txt", "content": "must roll back"},
                "record_sample_id": "test",
            },
        },
    )
    assert response.status_code == 422
    assert await call(studio, "get_tool_session", session_id="tools") == state
    assert studio[0].state.dataset == source


async def test_recorded_marker_and_control_token_text_stays_literal(studio):
    await call(studio, "create_tool_session", session_id="tools")
    content = (
        "literal {N_TICK:7} and {N_TICKS:8} and <|eos|> <|bos|> <|noop|> \\{N_TICK:9}"
    )
    written = await tool(
        studio,
        "file_write",
        {"path": "/data", "content": content},
        record_sample_id="test",
    )
    result = await tool(studio, "file_read", {"path": "/data"}, record_sample_id="test")
    assert (await call(studio, "get_anchors", sample_id="test"))["anchors"] == {}
    sample = (await call(studio, "get_trajectory", sample_id="test"))["sample"]
    events = [e for group in sample["replay"].values() for e in group]
    generated = next(
        e
        for e in events
        if e["window"] == written["recording"]["call_window"] and e["op"] == "generate"
    )
    function = json.loads(generated["content"].removeprefix("<tool>").removesuffix("</tool>"))
    assert function["args"]["content"] == content
    event = next(
        e
        for e in events
        if e["window"] == result["recording"]["result_window"] and e["op"] == "append"
    )
    outcome = json.loads(event["content"].removeprefix("<tool_result>").removesuffix("</tool_result>"))
    assert outcome["result"]["content"] == content


@pytest.mark.parametrize(
    "arguments",
    [
        {"max_calls": 257},
        {"max_bytes": 2_000_001},
        {"seconds_per_call": -1},
        {"clock_seed": "not-a-date"},
        {"session_id": ""},
        {"subagent_endpoint": "missing"},
    ],
)
async def test_bad_session_settings_do_not_create_sessions(studio, arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": "create_tool_session", "arguments": arguments}
    )
    assert response.status_code in (404, 422)
    assert (await call(studio, "list_tool_sessions"))["sessions"] == []


async def test_session_limit_and_duplicate_ids(studio):
    for index in range(8):
        await call(studio, "create_tool_session", session_id=str(index))
    response = await studio[1].post(
        "/api/mcp/call",
        json={"name": "create_tool_session", "arguments": {"session_id": "0"}},
    )
    assert response.status_code == 409
    response = await studio[1].post(
        "/api/mcp/call",
        json={"name": "create_tool_session", "arguments": {"session_id": "ninth"}},
    )
    assert response.json()["code"] == "quota_exceeded"


async def test_optional_subagent_is_bounded_and_receives_no_parent_state(studio):
    messages = []

    class FakeClient:
        async def complete(self, endpoint, prompt, tools=None):
            assert tools is None
            messages.extend(prompt)
            return {"content": "delegated result"}

    commands = studio[0].state.commands
    commands.client = FakeClient()
    endpoint = studio[0].state.dataset["config"]["endpoints"][0]["id"]
    await call(
        studio, "create_tool_session", session_id="tools", subagent_endpoint=endpoint
    )
    await tool(
        studio, "file_write", {"path": "/private", "content": "private virtual state"}
    )
    result = await tool(studio, "subagent", {"task": "inspect this task"})
    assert result["interaction"]["outcome"]["result"] == {
        "response": "delegated result"
    }
    assert "private virtual state" not in json.dumps(messages)
    assert messages[-1]["content"] == "inspect this task"
    assert next(
        d
        for d in (await call(studio, "list_simulated_tools", session_id="tools"))[
            "tools"
        ]
        if d["function"]["name"] == "subagent"
    )["available"]


async def test_inflight_calls_are_locked_and_revision_rechecked_after_provider(studio):
    entered, release = asyncio.Event(), asyncio.Event()

    class Blocked:
        async def complete(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return {"content": "completed"}

    commands = studio[0].state.commands
    commands.client = Blocked()
    endpoint = studio[0].state.dataset["config"]["endpoints"][0]["id"]
    await commands.call(
        "create_tool_session", {"session_id": "tools", "subagent_endpoint": endpoint}
    )
    pending = asyncio.create_task(
        commands.call(
            "call_simulated_tool",
            {
                "session_id": "tools",
                "name": "subagent",
                "arguments": {"task": "wait"},
                "record_sample_id": "test",
                "expected_revision": 0,
            },
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    try:
        for name, arguments in [
            ("reset_tool_session", {}),
            ("delete_tool_session", {}),
            ("call_simulated_tool", {"name": "time_now", "arguments": {}}),
        ]:
            with pytest.raises(CommandError) as error:
                await commands.call(name, {"session_id": "tools", **arguments})
            assert error.value.code == "busy"
        await commands.call(
            "update_trajectory_metadata",
            {"sample_id": "test", "patch": {"metadata": {"concurrent": True}}},
        )
        release.set()
        with pytest.raises(CommandError) as error:
            await pending
        assert error.value.code == "conflict"
        session = await commands.call("get_tool_session", {"session_id": "tools"})
        assert (
            session["calls"] == 0
            and session["interaction_count"] == 0
            and not session["busy"]
        )
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending


async def test_cancelled_call_preserves_session(studio):
    manager = ToolSessions()
    entered = asyncio.Event()

    async def broker(task, depth):
        entered.set()
        await asyncio.Event().wait()

    manager.create("tools", {"broker": broker})
    pending = asyncio.create_task(manager.call("tools", "subagent", {"task": "wait"}))
    await asyncio.wait_for(entered.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert manager.inspect("tools")["calls"] == 0
    assert not manager.inspect("tools")["busy"]


async def test_bounded_call_logs_and_shutdown_cleanup():
    manager = ToolSessions(max_log_bytes=400)
    manager.create("tools", {})
    first = await manager.call("tools", "time_now", {})
    second = await manager.call("tools", "time_now", {})
    assert manager.inspect("tools")["interaction_count"] == 1
    assert manager.interaction("tools", second["interaction"]["id"])
    with pytest.raises(ToolSessionError, match="evicted"):
        manager.interaction("tools", first["interaction"]["id"])
    sandbox = manager.get("tools").sandbox
    manager.close()
    assert sandbox.closed and manager.sessions == {}


@pytest.mark.parametrize(
    "message",
    [
        {"content": ""},
        {"content": "x" * 200_001},
        {"content": "text", "tool_calls": [{"id": "unexpected"}]},
    ],
)
async def test_subagent_rejects_invalid_or_unbounded_output(studio, message):
    class FakeClient:
        async def complete(self, *args, **kwargs):
            return message

    studio[0].state.commands.client = FakeClient()
    endpoint = studio[0].state.dataset["config"]["endpoints"][0]["id"]
    await call(
        studio, "create_tool_session", session_id="tools", subagent_endpoint=endpoint
    )
    result = await tool(studio, "subagent", {"task": "task"})
    assert not result["interaction"]["outcome"]["ok"]
    assert "must return" in result["interaction"]["outcome"]["error"]


async def test_provider_errors_do_not_echo_secrets(studio):
    class Failed:
        async def complete(self, *args, **kwargs):
            raise ValueError("provider echoed private-key")

    studio[0].state.commands.client = Failed()
    endpoint = studio[0].state.dataset["config"]["endpoints"][0]["id"]
    await call(
        studio, "create_tool_session", session_id="tools", subagent_endpoint=endpoint
    )
    result = await tool(studio, "subagent", {"task": "task"})
    assert not result["interaction"]["outcome"]["ok"]
    assert "private-key" not in json.dumps(result)
    assert "private-key" not in json.dumps(
        await call(studio, "get_tool_session", session_id="tools")
    )


async def test_dashboard_shutdown_destroys_tool_sessions(studio):
    app = studio[0]
    async with app.router.lifespan_context(app):
        await call(studio, "create_tool_session", session_id="tools")
        sandbox = app.state.tool_sessions.get("tools").sandbox
    assert sandbox.closed and app.state.tool_sessions.sessions == {}
