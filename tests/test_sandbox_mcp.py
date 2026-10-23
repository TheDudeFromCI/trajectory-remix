from trajectory_remix.storage import DatasetStore
import copy
import json


async def call(studio, name, **arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": name, "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_scheduled_calls_share_source_history_and_reject_stale_edits(
    studio, tmp_path
):
    result = await call(
        studio,
        "create_tool_call",
        sample_id="test",
        tick="150",
        tool="file_write",
        arguments={"path": "mcp.txt", "content": "saved"},
        duration_ticks=50,
        expected_revision=0,
    )
    tool_call = result["tool_call"]
    assert tool_call["start"] > tool_call["request_start"] == 150
    assert tool_call["end"] == tool_call["start"] + 50
    assert result["revision"] == 1 and result["saved"]
    old = await call(studio, "get_sandbox_state", sample_id="test", tick=tool_call["start"])
    assert old["sandbox"]["files"] == {}
    assert old["sandbox"]["tool_calls"][-1]["status"] == "running"
    current = await call(
        studio, "get_sandbox_state", sample_id="test", tick=tool_call["end"], include_calls=False
    )
    assert current["sandbox"]["files"] == {"/mcp.txt": "saved"}
    assert "tool_calls" not in current["sandbox"]
    before = copy.deepcopy(studio[0].state.dataset)
    rejected = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "create_tool_call",
            "arguments": {
                "sample_id": "test",
                "tick": "201",
                "tool": "file_list",
                "arguments": {},
                "expected_revision": 0,
            },
        },
    )
    assert rejected.status_code == 409
    assert studio[0].state.dataset == before
    await call(studio, "undo_edits")
    assert (await call(studio, "get_trajectory", sample_id="test"))["sample"][
        "replay"
    ].get("150") is None
    await call(studio, "redo_edits")
    assert (await call(studio, "get_sandbox_state", sample_id="test", tick=tool_call["end"]))[
        "sandbox"
    ]["files"] == {"/mcp.txt": "saved"}
    await call(studio, "save_dataset")
    saved = {"samples": list(DatasetStore(tmp_path / "project.kidra-dataset").samples)}
    assert saved["samples"][0]["replay"]["150"][0]["op"] == "tool-call"


async def test_scheduled_calls_compile_atomically_in_batches(studio):
    before = copy.deepcopy(studio[0].state.dataset)
    result = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "create_tool_call",
            "arguments": {
                "sample_id": "test",
                "tick": "1",
                "tool": "file_list",
                "arguments": {},
                "duration_ticks": -1,
            },
        },
    )
    assert result.status_code == 422 and studio[0].state.dataset == before
    await call(
        studio,
        "apply_batch",
        edits=[
            {
                "name": "create_tool_call",
                "arguments": {
                    "sample_id": "test",
                    "tick": "1",
                    "tool": "file_write",
                    "arguments": {"path": "x", "content": "old"},
                    "window": "first",
                    "content": "x",
                },
            },
            {
                "name": "create_tool_call",
                "arguments": {
                    "sample_id": "test",
                    "tick": "3",
                    "tool": "file_read",
                    "arguments": {"path": "x"},
                    "window": "read",
                    "content": "x",
                },
            },
        ],
    )
    assert studio[0].state.dataset["revision"] == 1
    frames = await call(
        studio, "preview_snapshots", sample_id="test", start_tick=0, end_tick=4
    )
    assert frames["frames"][0]["sandbox"]["files"] == {}
    assert frames["frames"][4]["sandbox"]["files"] == {"/x": "old"}
    assert (
        frames["frames"][4]["sandbox"]["tool_calls"][-1]["outcome"]["result"]["content"]
        == "old"
    )


async def test_streamed_call_schema_history_and_shared_window(studio):
    first = await call(
        studio, "create_tool_call", sample_id="test", tick="1",
        tool="time_now", arguments={}, window="tools", delivery="stream",
        segments=[{"text": "abc<|noop|>{N_TICK:1}", "loss": {"mask": "include"}}],
        duration_ticks=2,
        auto_anchor=True,
    )
    assert first["tool_call"]["request_start"] == 1
    assert first["tool_call"]["start"] == 5
    assert first["tool_call"]["end"] == 7
    composing = await call(studio, "get_sandbox_state", sample_id="test", tick=3)
    assert composing["sandbox"]["tool_calls"][0]["status"] == "composing"
    assert "outcome" not in composing["sandbox"]["tool_calls"][0]
    # Reusing the same window returns the newly scheduled call, not the first one.
    second = await call(
        studio, "create_tool_call", sample_id="test", tick="{N_TICK:2}+1",
        tool="file_list", arguments={}, window="tools",
    )
    assert second["tool_call"]["tool"] == "file_list"
    assert second["tool_call"]["request_start"] == first["tool_call"]["result_end"] + 1
    assert second["tool_call"]["start"] > second["tool_call"]["request_start"]
    before = copy.deepcopy(studio[0].state.dataset)
    rejected = await studio[1].post("/api/samples/0/tool-calls", json={
        "tick": "9", "tool": "time_now", "arguments": {},
        "content": "x", "segments": [{"text": "y"}],
    })
    assert rejected.status_code == 422
    assert studio[0].state.dataset == before


async def test_auto_anchor_avoids_existing_and_new_request_markers_and_is_undoable(studio):
    first = await call(
        studio, "create_tool_call", sample_id="test", tick="1", tool="file_list",
        arguments={}, content="x{N_TICK:9}", auto_anchor=True,
        delivery="stream", tokens_per_tick=4,
    )
    assert first["tool_call"]["result_anchor"] == 10
    event = studio[0].state.dataset["samples"][0]["replay"]["1"][0]
    assert event["result-anchor"] == 10
    anchors = await call(studio, "get_anchors", sample_id="test")
    assert anchors["anchors"]["10"]["tick"] == first["tool_call"]["result_end"]
    second = await call(
        studio, "create_tool_call", sample_id="test", tick="{N_TICK:10}+1",
        tool="time_now", arguments={}, auto_anchor=True,
    )
    assert second["tool_call"]["result_anchor"] == 11
    await call(studio, "undo_edits")
    assert "{N_TICK:10}+1" not in studio[0].state.dataset["samples"][0]["replay"]
    await call(studio, "redo_edits")
    assert studio[0].state.dataset["samples"][0]["replay"]["{N_TICK:10}+1"][0]["result-anchor"] == 11
