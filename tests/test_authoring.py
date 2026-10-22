import copy
import json

import httpx
import pytest

from trajectory_remix.server import create_app


@pytest.fixture
async def studio(compiler, tmp_path, sample):
    app = create_app(compiler, tmp_path / "project.kidra-dataset")
    app.state.dataset["samples"].append(copy.deepcopy(sample))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://testserver",
        headers={"X-Remix-Token": app.state.csrf},
    ) as client:
        yield app, client


async def call(studio, name, **arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": name, "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def source(studio, sample_id="test"):
    return (await call(studio, "get_trajectory", sample_id=sample_id))["sample"]


async def test_trajectory_crud_metadata_and_save(studio, tmp_path):
    created = await call(studio, "create_trajectory", sample_id="empty")
    assert created == {"sample_id": "empty", "revision": 1, "saved": False}
    report = await call(studio, "validate_trajectory", sample_id="empty")
    assert not report["valid"]
    await call(
        studio,
        "create_event",
        sample_id="empty",
        tick="0",
        event={"op": "open", "window": "chat"},
        validate=True,
    )
    await call(
        studio,
        "update_trajectory_metadata",
        sample_id="empty",
        patch={"metadata": {"tags": ["reviewed"], "nested": {"keep": 1, "drop": 2}}},
    )
    await call(
        studio,
        "update_trajectory_metadata",
        sample_id="empty",
        patch={"metadata": {"nested": {"drop": None}}},
    )
    metadata = await call(studio, "get_trajectory_metadata", sample_id="empty")
    assert "replay" not in metadata["attributes"]
    assert metadata["attributes"]["metadata"]["nested"] == {"keep": 1}
    found = await call(studio, "list_trajectories", query="reviewed", limit=1)
    assert found["total"] == 1 and found["trajectories"][0]["id"] == "empty"
    await call(studio, "duplicate_trajectory", sample_id="empty", new_sample_id="copy")
    await call(
        studio,
        "update_trajectory_metadata",
        sample_id="copy",
        patch={"metadata": {"tags": []}},
    )
    assert (await source(studio, "empty"))["metadata"]["tags"] == ["reviewed"]
    replacement = await source(studio, "copy")
    replacement["metadata"] = {"title": "replaced"}
    await call(
        studio,
        "replace_trajectory",
        sample_id="copy",
        sample=replacement,
        validate=True,
    )
    await call(studio, "delete_trajectory", sample_id="test")
    saved = await call(studio, "save_dataset")
    data = json.loads((tmp_path / "project.kidra-dataset").read_text())
    assert saved["saved"] and saved["revision"] == studio[0].state.dataset["revision"]
    assert [s["id"] for s in data["samples"]] == ["empty", "copy"]
    assert data["samples"][1]["metadata"] == {"title": "replaced"}


async def test_demo_template_uses_configured_eos(studio):
    await call(
        studio, "create_trajectory", template="demo", sample_id="demo", validate=True
    )
    assert (await call(studio, "validate_trajectory", sample_id="demo"))["valid"]


async def test_event_editing_moving_offsets_and_deletion(studio):
    event = {"op": "append", "window": "a", "source": "tool", "content": "hello"}
    await call(
        studio, "create_event", sample_id="test", tick="3", event=event, validate=True
    )
    await call(
        studio,
        "update_event",
        sample_id="test",
        tick="3",
        event_index=0,
        settings={"window": "b", "delivery": "stream", "tokens-per-tick": 2},
    )
    moved = await call(
        studio,
        "move_event",
        sample_id="test",
        tick="3",
        event_index=0,
        tick_offset=-2,
        validate=True,
    )
    assert moved["tick"] == "1"
    assert "3" not in (await source(studio))["replay"]
    await call(
        studio,
        "update_event",
        sample_id="test",
        tick="1",
        event_index=0,
        settings={},
        remove_fields=["delivery", "tokens-per-tick"],
        validate=True,
    )
    await call(
        studio,
        "duplicate_event",
        sample_id="test",
        tick="1",
        event_index=0,
        to_tick="2",
    )
    await call(
        studio, "delete_event", sample_id="test", tick="1", event_index=0, validate=True
    )
    assert "1" not in (await source(studio))["replay"]
    await call(
        studio,
        "rename_window",
        sample_id="test",
        window="b",
        new_window="tool",
        validate=True,
    )
    event = await call(studio, "get_event", sample_id="test", tick="2", event_index=0)
    assert event["event"]["window"] == "tool"
    listed = await call(
        studio, "list_events", sample_id="test", window="tool", offset=1, limit=1
    )
    assert listed["total"] == 2 and listed["events"][0]["tick"] == "2"
    await call(
        studio,
        "move_event",
        sample_id="test",
        tick="0",
        event_index=1,
        position=0,
        validate=True,
    )
    assert (await source(studio))["replay"]["0"][0]["window"] == "tool"


async def test_anchor_offsets_and_preview_retain_prefix(studio):
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="1",
        event={
            "op": "append",
            "window": "a",
            "source": "tool",
            "delivery": "stream",
            "content": "abc{N_TICK:7}",
        },
        validate=True,
    )
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="{N_TICK:7}+1",
        event={
            "op": "generate",
            "window": "b",
            "content": "OK<|eos|>",
        },
        validate=True,
    )
    await call(
        studio,
        "move_event",
        sample_id="test",
        tick="{N_TICK:7}+1",
        event_index=0,
        tick_offset=2,
        validate=True,
    )
    assert "{N_TICK:7}+3" in (await source(studio))["replay"]
    anchors = await call(studio, "get_anchors", sample_id="test")
    assert anchors["anchors"]["7"]["tick"] == 3
    assert anchors["anchors"]["7"]["phase"] == "B"
    frames = await call(
        studio, "preview_snapshots", sample_id="test", start_tick=6, end_tick=8
    )
    assert [f["tick"] for f in frames["frames"]] == [6, 7, 8]
    assert frames["frames"][0]["windows"]["a"]["text"] == "abc"
    assert frames["frames"][0]["windows"]["b"]["text"] == "O"
    assert frames["frames"][-1]["windows"]["b"]["status"] == "closed"
    assert frames["frames"][-1]["windows"]["b"]["kv_tokens"] == 0
    history = await call(
        studio,
        "get_window_history",
        sample_id="test",
        window="b",
        start_tick=7,
        end_tick=8,
    )
    assert history["text"] == "OK" and history["window"]["close"] == 8
    assert len(history["rows"]) == 2 and all(
        r["window"] == "b" for r in history["rows"]
    )
    filtered = await call(
        studio,
        "preview_snapshots",
        sample_id="test",
        start_tick=0,
        end_tick=8,
        step=4,
        windows=["b"],
    )
    assert filtered["frames"][0]["windows"]["b"]["status"] == "open"
    assert all(list(f["windows"]) == ["b"] for f in filtered["frames"])


async def test_text_and_segment_patches(studio):
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="1",
        event={
            "op": "generate",
            "window": "a",
            "content": "a🙂cdef",
            "loss": {"weight": 0.5},
        },
    )
    await call(
        studio,
        "patch_event_content",
        sample_id="test",
        tick="1",
        event_index=0,
        expected_text="a🙂cdef",
        patches=[
            {"start": 1, "end": 2, "text": "b"},
            {"start": 5, "end": 6, "text": "!"},
        ],
    )
    event = (await source(studio))["replay"]["1"][0]
    assert event["content"] == "abcde!" and event["loss"]["weight"] == 0.5
    await call(
        studio,
        "patch_event_content",
        sample_id="test",
        tick="1",
        event_index=0,
        segments=[{"text": "one", "loss": {"mask": "exclude"}}, {"text": "two"}],
    )
    await call(
        studio,
        "patch_event_content",
        sample_id="test",
        tick="1",
        event_index=0,
        segment_index=1,
        patches=[{"start": 0, "end": 3, "text": "three"}],
        validate=True,
    )
    event = (await source(studio))["replay"]["1"][0]
    assert "content" not in event and event["segments"][1]["text"] == "three"
    assert event["segments"][0]["loss"]["mask"] == "exclude"
    await call(
        studio,
        "patch_event_content",
        sample_id="test",
        tick="1",
        event_index=0,
        content="",
        validate=True,
    )
    event = (await source(studio))["replay"]["1"][0]
    assert event["content"] == "" and "segments" not in event


async def test_invalid_draft_diagnostics_and_cache_invalidation(studio):
    warnings = await call(
        studio, "get_diagnostics", sample_id="test", severity="warning"
    )
    assert warnings["valid"] and len(warnings["diagnostics"]) == 2
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="1",
        event={
            "op": "generate",
            "window": "a",
            "content": "broken{N_TICKS:1}",
        },
    )
    report = await call(studio, "validate_trajectory", sample_id="test")
    assert not report["valid"] and report["diagnostics"][0]["path"] == "replay[1][0]"
    assert "Invalid marker" in report["diagnostics"][0]["message"]
    assert (await source(studio))["replay"]["1"][0]["content"] == "broken{N_TICKS:1}"
    await call(
        studio,
        "patch_event_content",
        sample_id="test",
        tick="1",
        event_index=0,
        content="fixed<|eos|>",
        validate=True,
    )
    assert (await call(studio, "validate_trajectory", sample_id="test"))["valid"]


@pytest.mark.parametrize(
    "name,arguments,status",
    [
        (
            "create_event",
            {
                "sample_id": "test",
                "tick": "1",
                "event": {"op": "close", "window": "a"},
                "validate": True,
            },
            422,
        ),
        ("create_event", {"sample_id": "test", "tick": "-1", "event": {}}, 422),
        (
            "move_event",
            {"sample_id": "test", "tick": "0", "event_index": 0, "tick_offset": -1},
            422,
        ),
        (
            "move_event",
            {
                "sample_id": "test",
                "tick": "0",
                "event_index": 0,
                "to_tick": "1",
                "tick_offset": 1,
            },
            422,
        ),
        (
            "move_event",
            {"sample_id": "test", "tick": "0", "event_index": 0, "position": 100},
            422,
        ),
        ("duplicate_trajectory", {"sample_id": "test", "new_sample_id": "test"}, 409),
        ("rename_window", {"sample_id": "test", "window": "a", "new_window": "b"}, 409),
        ("get_trajectory", {"sample_id": "missing"}, 404),
        ("get_event", {"sample_id": "test", "tick": "0", "event_index": -1}, 422),
        ("get_event", {"sample_id": "test", "tick": "0", "event_index": True}, 422),
        ("delete_event", {"sample_id": "test", "tick": "0", "event_index": 4}, 404),
        (
            "update_event",
            {
                "sample_id": "test",
                "tick": "0",
                "event_index": 0,
                "settings": {"content": "bad"},
            },
            422,
        ),
        (
            "update_trajectory_metadata",
            {"sample_id": "test", "patch": {"replay": {}}},
            422,
        ),
        ("update_trajectory_metadata", {"sample_id": "test", "patch": {"id": ""}}, 422),
        ("delete_trajectory", {"sample_id": "test", "expected_revision": 42}, 409),
        ("create_trajectory", {"sample_id": "empty", "validate": True}, 422),
        ("list_events", {"sample_id": "test", "limit": 5001}, 422),
        ("get_trajectory", {"sample_id": "test", "unknown": 1}, 422),
        (
            "preview_snapshots",
            {"sample_id": "test", "start_tick": 0, "end_tick": 1},
            422,
        ),
        (
            "preview_snapshots",
            {
                "sample_id": "test",
                "start_tick": 0,
                "end_tick": 0,
                "windows": ["missing"],
            },
            404,
        ),
        ("get_window_history", {"sample_id": "test", "window": "missing"}, 404),
        ("unknown_command", {}, 422),
    ],
)
async def test_rejected_commands_are_atomic(studio, name, arguments, status):
    before = copy.deepcopy(studio[0].state.dataset)
    response = await studio[1].post(
        "/api/mcp/call", json={"name": name, "arguments": arguments}
    )
    assert response.status_code == status, response.text
    assert studio[0].state.dataset == before


@pytest.mark.parametrize(
    "changes",
    [
        {
            "patches": [
                {"start": 0, "end": 3, "text": "a"},
                {"start": 2, "end": 4, "text": "b"},
            ]
        },
        {"patches": [{"start": 0, "end": 100, "text": "a"}]},
        {"patches": [], "expected_text": "stale"},
        {"content": "new", "segments": []},
        {"content": "new", "segment_index": 0},
    ],
)
async def test_bad_content_patches_do_not_change_source(studio, changes):
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="1",
        event={
            "op": "generate",
            "window": "a",
            "content": "original",
        },
    )
    before = await source(studio)
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "patch_event_content",
            "arguments": {
                "sample_id": "test",
                "tick": "1",
                "event_index": 0,
                **changes,
            },
        },
    )
    assert response.status_code in (409, 422)
    assert await source(studio) == before


async def test_generation_lock_and_http_security(studio):
    app, client = studio
    app.state.runner.status["running"] = True
    response = await client.post(
        "/api/mcp/call",
        json={"name": "delete_trajectory", "arguments": {"sample_id": "test"}},
    )
    assert response.status_code == 409
    assert (await call(studio, "get_trajectory", sample_id="test"))["sample"][
        "id"
    ] == "test"
    for path in ("/api/mcp/tools", "/api/mcp/call"):
        method = client.get if path.endswith("tools") else client.post
        assert (await method(path, headers={"X-Remix-Token": ""})).status_code == 403
        assert (
            await method(path, headers={"Origin": "https://evil.example"})
        ).status_code == 403
        assert (await method(path, headers={"Host": "evil.example"})).status_code == 400


async def test_browser_revision_conflict_preserves_mcp_edit(studio):
    response = await studio[1].get("/api/samples/0")
    revision = int(response.headers["X-Remix-Revision"])
    old_source = response.json()
    await call(
        studio,
        "update_trajectory_metadata",
        sample_id="test",
        patch={"metadata": {"title": "MCP edit"}},
    )
    response = await studio[1].put(
        "/api/samples/0", json={"sample": old_source, "expected_revision": revision}
    )
    assert response.status_code == 409
    assert (await source(studio))["metadata"]["title"] == "MCP edit"


async def test_preview_and_history_limits(studio):
    await call(
        studio,
        "create_event",
        sample_id="test",
        tick="150",
        event={
            "op": "append",
            "window": "a",
            "source": "user",
            "content": "later",
        },
    )
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "preview_snapshots",
            "arguments": {
                "sample_id": "test",
                "start_tick": 0,
                "end_tick": 150,
            },
        },
    )
    assert response.status_code == 422 and "100 frames" in response.text
    history = await call(
        studio,
        "get_window_history",
        sample_id="test",
        window="a",
        include_noops=True,
        start_tick=0,
        end_tick=149,
        offset=5,
        limit=2,
    )
    assert len(history["rows"]) == 2 and history["total_rows"] > 100
    assert all(r["op"] == "noop" for r in history["rows"])


async def test_semantic_validation_is_opt_in(studio, monkeypatch):
    calls = []

    async def validators(self, sample):
        calls.append(sample["id"])
        return {"valid": False, "reports": [{"feedback": ["semantic failure"]}]}

    monkeypatch.setattr(
        "trajectory_remix.authoring.SampleBuilder.validators", validators
    )
    assert (await call(studio, "validate_trajectory", sample_id="test"))["valid"]
    assert calls == []
    report = await call(studio, "validate_trajectory", sample_id="test", semantic=True)
    assert not report["valid"] and calls == ["test"]
