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
    assert result["tool_call"]["end"] == 200
    assert result["revision"] == 1 and not result["saved"]
    old = await call(studio, "get_sandbox_state", sample_id="test", tick=175)
    assert old["sandbox"]["files"] == {}
    assert old["sandbox"]["tool_calls"][-1]["status"] == "running"
    current = await call(
        studio, "get_sandbox_state", sample_id="test", tick=200, include_calls=False
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
    assert (await call(studio, "get_sandbox_state", sample_id="test", tick=200))[
        "sandbox"
    ]["files"] == {"/mcp.txt": "saved"}
    await call(studio, "save_dataset")
    saved = json.loads((tmp_path / "project.kidra-dataset").read_text())
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
                },
            },
            {
                "name": "create_tool_call",
                "arguments": {
                    "sample_id": "test",
                    "tick": "2",
                    "tool": "file_read",
                    "arguments": {"path": "x"},
                    "window": "read",
                },
            },
        ],
    )
    assert studio[0].state.dataset["revision"] == 1
    frames = await call(
        studio, "preview_snapshots", sample_id="test", start_tick=0, end_tick=2
    )
    assert frames["frames"][0]["sandbox"]["files"] == {}
    assert frames["frames"][2]["sandbox"]["files"] == {"/x": "old"}
    assert (
        frames["frames"][2]["sandbox"]["tool_calls"][-1]["outcome"]["result"]["content"]
        == "old"
    )
