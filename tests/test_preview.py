import copy
import json

import pytest

from trajectory_remix.compiler import CompileError, FrameIndex, chunks, visible_to
from trajectory_remix.preview import PreviewCache


def test_large_preview_retains_managed_note_updates(compiler, large_sample):
    large_sample["replay"].update({
        "19990": [{"op": "invisible-tool-call", "tool": "note_save", "arguments": {
            "name": "review", "description": "Inspect preview", "priority": "high", "status": "not started",
        }}],
        "19992": [{"op": "invisible-tool-call", "tool": "note_save", "arguments": {
            "name": "review", "status": "doing",
        }}],
        "19994": [{"op": "invisible-tool-call", "tool": "note_delete", "arguments": {"name": "review"}}],
    })
    preview = PreviewCache(compiler).compile(large_sample)
    assert len(preview.plan["rows"]) > 50_000
    for tick in (19994, 19989, 19990, 19992, 0):
        frame = preview.frame(tick)
        assert frame == compiler.frame(preview.plan, tick)
        text = frame["windows"]["notes"]["text"]
        tasks = json.loads(text[text.index("{"):])["high"]
        if 19990 <= tick < 19994:
            assert tasks["review"]["status"] == ("doing" if tick >= 19992 else "not started")
        else:
            assert tasks == {}
    assert all(row["loss_mask"] == 0 for row in preview.index.rows["notes"])


def test_indexed_large_replay_preserves_context_and_exports(compiler, large_sample):
    plan = compiler.compile(large_sample)
    original = copy.deepcopy(plan)
    assert len(plan["rows"]) > 50_000
    index = FrameIndex(plan)
    assert sum(map(len, index.rows.values())) < 1000
    # Out-of-order seeks across stream tokens, replacement, EOS, opening, and
    # running/completed/failed tools must match the canonical replay exactly.
    for tick in [20015, 0, 20000, 20001, 20002, 20003, 20004, 20007, 20008, 20010, 20011, 1]:
        assert compiler.frame(plan, tick, index=index) == compiler.frame(plan, tick)
    assert "old" in compiler.frame(plan, 20001, index=index)["windows"]["b"]["text"]
    assert "old" not in compiler.frame(plan, 20002, index=index)["windows"]["b"]["text"]
    assert compiler.frame(plan, 20003, index=index)["sandbox"].get("files", {}) == {}
    assert compiler.frame(plan, 20004, index=index)["sandbox"]["files"] == {"/x": "new"}
    running = compiler.frame(plan, 20007, index=index)["tool_calls"][-1]
    assert running["status"] == "running" and "outcome" not in running
    assert compiler.frame(plan, 20008, index=index)["tool_calls"][-1]["status"] == "failed"
    model = [r for r in plan["rows"] if r["window"] == "a" and r["op"] == "generate"]
    assert [r["loss_mask"] for r in model] == [1, 0, 1, 1]
    assert model[0]["loss_weight"] == 0.25
    assert any(r["op"] == "noop" and r["loss_mask"] == 1 for r in plan["rows"])
    assert any(visible_to(model[0], r, plan["windows"]) for r in plan["rows"] if r["window"] == "c")
    assert [r for chunk in chunks(plan, 64) for r in chunk["rows"]] == original["rows"]
    assert plan == original


async def test_large_api_seek_and_bounded_playback_share_compilation(studio, large_sample, monkeypatch):
    app, client = studio
    await client.put("/api/samples/0", json={"sample": large_sample})
    source = (await client.get("/api/samples/0")).json()
    reference = app.state.compiler.compile(source)
    compile_original = app.state.compiler.compile
    compilations = []

    def counted(sample):
        compilations.append(sample["id"])
        return compile_original(sample)

    monkeypatch.setattr(app.state.compiler, "compile", counted)
    response = await client.post("/api/compile", json={"sample": source})
    assert response.status_code == 200, response.text
    metadata = response.json()
    assert metadata["valid"] and metadata["preview"]["available"]
    assert metadata["row_count"] == len(reference["rows"]) > 50_000
    assert len(response.content) < 10_000
    assert not {"rows", "sandbox", "frame"} & metadata.keys()
    assert all("text" not in event for event in metadata["events"])
    assert all("arguments" not in c and set(c["outcome"]) == {"ok"} for c in metadata["tool_calls"])
    plan_id = metadata["plan_id"]
    for tick in (20015, 20001, 0, 20011, 20002):
        response = await client.get(f"/api/frame/{plan_id}/{tick}")
        assert response.status_code == 200
        assert response.json() == app.state.compiler.frame(reference, tick)
    # Incremental playback starts near the selected tick, not at tick zero.
    for start in (19998, 20006):
        response = await client.get(f"/api/frames/{plan_id}?start_tick={start}&end_tick={start+7}")
        assert response.status_code == 200
        assert response.json()["frames"] == [app.state.compiler.frame(reference, t) for t in range(start, start + 8)]
    assert (await client.get(f"/api/frames/{plan_id}?start_tick=0&end_tick=16")).status_code == 413
    assert (await client.get(f"/api/frames/{plan_id}?start_tick=5&end_tick=4")).status_code == 422
    assert (await client.get(f"/api/frame/{plan_id}/-1")).status_code == 422
    assert (await client.get(f"/api/frame/{plan_id}/20016")).status_code == 422
    assert (await client.post("/api/compile", json={"sample": source})).json()["plan_id"] == plan_id
    snapshots = await client.post("/api/mcp/call", json={"name": "preview_snapshots", "arguments": {
        "sample_id": "large", "start_tick": 20001, "end_tick": 20004, "windows": ["a", "b"],
    }})
    assert snapshots.status_code == 200, snapshots.text
    for frame in snapshots.json()["frames"]:
        assert set(frame["windows"]) == {"a", "b"}
        for wid, window in frame["windows"].items():
            expected = app.state.compiler.frame(reference, frame["tick"])["windows"][wid]
            assert window["text"] == expected["text"]
            assert window["cached_tokens"] == expected["cached_tokens"]
        assert frame["windows"]["a"]["status"] == ("closed" if frame["tick"] >= 20003 else "open")
        assert frame["windows"]["a"]["kv_tokens"] == (0 if frame["tick"] >= 20003 else frame["windows"]["a"]["cached_tokens"])
    history = await client.post("/api/mcp/call", json={"name": "get_window_history", "arguments": {
        "sample_id": "large", "window": "b", "start_tick": 100, "end_tick": 19999,
        "include_noops": True, "offset": 1234, "limit": 2,
    }})
    assert history.status_code == 200, history.text
    assert len(history.json()["rows"]) == 2 and history.json()["total_rows"] == 19900
    assert [r["tick"] for r in history.json()["rows"]] == [1334, 1335]
    assert compilations == ["large"]
    # Expired IDs are explicit, and recompiling restores the same immutable plan.
    app.state.commands.previews.entries[plan_id].last_used -= 901
    expired = await client.get(f"/api/frame/{plan_id}/0")
    assert expired.status_code == 404 and "compile the sample again" in expired.text
    assert expired.json()["code"] == "preview_expired"
    expired_range = await client.get(f"/api/frames/{plan_id}?start_tick=0&end_tick=1")
    assert expired_range.status_code == 404
    assert expired_range.json()["code"] == "preview_expired"
    assert (await client.post("/api/compile", json={"sample": source})).status_code == 200
    restored = await client.get(f"/api/frame/{plan_id}/20011")
    assert restored.json() == app.state.compiler.frame(reference, 20011)
    assert compilations == ["large", "large"]


def test_cache_budget_lru_expiry_and_source_changes(compiler, sample, monkeypatch):
    import trajectory_remix.preview as preview_module

    now = [0]
    monkeypatch.setattr(preview_module.time, "monotonic", lambda: now[0])
    cache = PreviewCache(compiler, max_entries=2)
    first = cache.compile(sample)
    assert cache.compile(copy.deepcopy(sample)) is first
    sample["id"] = "second"
    second = cache.compile(sample)
    cache.get(first.plan_id)
    sample["id"] = "third"
    third = cache.compile(sample)
    assert cache.get(second.plan_id) is None
    assert cache.get(first.plan_id) is first
    # A row budget, not just the number of plans, bounds retained expansion.
    cache.max_rows = len(third.plan["rows"])
    sample["id"] = "fourth"
    fourth = cache.compile(sample)
    assert list(cache.entries) == [fourth.plan_id]
    assert cache.row_count == len(fourth.plan["rows"])
    now[0] = 901
    cache.purge()
    assert not cache.entries and cache.row_count == 0
    # Revision invalidation also runs tool-store dependency checks/pinning when
    # an unsaved preview is saved or tool definitions are repaired by an edit.
    assert cache.compile(sample, revision=1).plan_id != cache.compile(sample, revision=2).plan_id


async def test_preview_output_limits_do_not_invalidate_compilation(studio, sample, monkeypatch):
    import trajectory_remix.preview as preview_module

    app, client = studio
    monkeypatch.setattr(preview_module, "MAX_PREVIEW_BYTES", 10)
    response = await client.post("/api/compile", json={"sample": sample})
    assert response.status_code == 200
    assert response.json()["valid"] and not response.json()["preview"]["available"]
    assert "Timeline metadata" in response.json()["preview"]["message"]
    plan_id = response.json()["plan_id"]
    response = await client.get(f"/api/frame/{plan_id}/0")
    assert response.status_code == 413 and response.json()["code"] == "preview_limit"
    assert "selected windows" in response.json()["detail"]


async def test_frame_range_byte_budget_and_single_frame_fallback(studio, sample, monkeypatch):
    app, client = studio
    # Use real UTF-8 serialization and the production 4 MB budget. A range can
    # exceed it even though each individual snapshot fits comfortably.
    original_frame = app.state.compiler.frame

    def large_frame(*args, **kwargs):
        frame = original_frame(*args, **kwargs)
        if "a" in frame["windows"]:
            frame["windows"]["a"]["text"] = "é" * 300_000
        return frame

    monkeypatch.setattr(app.state.compiler, "frame", large_frame)
    sample["replay"]["10"] = [{"op": "append", "window": "a", "source": "user", "content": "later"}]
    await client.put("/api/samples/0", json={"sample": sample})
    metadata = (await client.post("/api/compile", json={"sample": sample})).json()
    assert metadata["valid"] and metadata["preview"]["available"]
    plan_id = metadata["plan_id"]
    limited = await client.get(f"/api/frames/{plan_id}?start_tick=0&end_tick=7")
    assert limited.status_code == 413 and "fewer frames" in limited.text
    single = await client.get(f"/api/frame/{plan_id}/0")
    assert single.status_code == 200 and len(single.content) < 4_000_000
    small = await client.get(f"/api/frames/{plan_id}?start_tick=0&end_tick=1")
    assert small.status_code == 200 and len(small.content) < 4_000_000
    assert small.json()["frames"][0] == single.json()
    async def snapshots(end_tick, windows=None):
        return await client.post("/api/mcp/call", json={"name": "preview_snapshots", "arguments": {
            "sample_id": "test", "start_tick": 0, "end_tick": end_tick, "windows": windows,
        }})
    assert (await snapshots(7)).status_code == 422
    assert (await snapshots(1)).status_code == 200
    assert (await snapshots(7, ["b"])).status_code == 200
    # A genuinely oversized frame also leaves compilation valid and actionable.
    def oversized_frame(*args, **kwargs):
        frame = original_frame(*args, **kwargs)
        frame["sandbox"]["large_artifact"] = "é" * 2_000_001
        return frame
    monkeypatch.setattr(app.state.compiler, "frame", oversized_frame)
    assert (await client.post("/api/compile", json={"sample": sample})).json()["valid"]
    oversized = await client.get(f"/api/frame/{plan_id}/0")
    assert oversized.status_code == 413 and "selected windows" in oversized.text


def test_compiler_safety_limits_remain_enforced(compiler, sample):
    from trajectory_remix.compiler import MAX_ROWS, MAX_TICKS

    assert MAX_ROWS == 500_000 and MAX_TICKS == 100_000
    sample["replay"]["99999"] = [{"op": "append", "source": "user", "window": "a", "content": "end"}]
    sample["replay"]["0"] += [{"op": "open", "window": str(i)} for i in range(4)]
    with pytest.raises(CompileError, match="Expanded row limit.*implicit NOOPs.*Close windows"):
        compiler.compile(sample)
    sample["replay"]["100000"] = sample["replay"].pop("99999")
    with pytest.raises(CompileError, match="Tick limit"):
        compiler.compile(sample)


def test_preview_at_full_compiler_row_capacity(compiler):
    sample = {
        "schema-version": "1.0", "tick-policy": "external-first", "id": "capacity",
        "defaults": {"window-headers": False},
        "replay": {
            "0": [{"op": "open", "window": w} for w in "abcde"],
            "99998": [{"op": "append", "window": "a", "source": "user", "content": "x"}],
        },
    }
    preview = PreviewCache(compiler).compile(sample)
    assert len(preview.plan["rows"]) == 500_000
    assert preview.metadata()["row_count"] == 500_000
    assert sum(map(len, preview.index.rows.values())) == 6
    assert preview.frame(99998)["windows"]["a"] == {"text": "x", "cached_tokens": 2}
    assert preview.frame(0)["windows"]["a"] == {"text": "", "cached_tokens": 1}
