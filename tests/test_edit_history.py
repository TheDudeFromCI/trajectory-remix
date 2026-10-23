import copy
import json
import random

import pytest

from trajectory_remix.changes import SampleHistory, apply_changes, json_changes


async def call(studio, name, **arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": name, "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_batch_validates_only_final_state_and_undoes_once(studio):
    before = copy.deepcopy(studio[0].state.dataset["samples"])
    edits = [
        {"name": "create_trajectory", "arguments": {"sample_id": "batched"}},
        {
            "name": "create_event",
            "arguments": {
                "sample_id": "batched",
                "tick": "1",
                "event": {
                    "op": "generate",
                    "window": "chat",
                    "content": "OK<|eos|>",
                },
            },
        },
        {
            "name": "create_event",
            "arguments": {
                "sample_id": "batched",
                "tick": "0",
                "event": {
                    "op": "open",
                    "window": "chat",
                },
            },
        },
    ]
    preview = await call(
        studio, "apply_batch", edits=edits, dry_run=True, expected_revision=0
    )
    assert preview["revision"] == 0
    assert studio[0].state.dataset["samples"] == before
    assert not (await call(studio, "get_edit_history"))["can_undo"]
    applied = await call(studio, "apply_batch", edits=edits, expected_revision=0)
    assert applied["revision"] == 1 and all(
        "revision" not in r for r in applied["results"]
    )
    after = copy.deepcopy(studio[0].state.dataset["samples"])
    assert apply_changes(before, applied["changes"]) == after
    history = await call(studio, "get_edit_history", include_changes=True)
    assert len(history["undo"]) == 1 and history["undo"][0]["label"] == "Batch: 3 edits"
    assert history["undo"][0]["changes"] == applied["changes"]
    assert (await call(studio, "validate_trajectory", sample_id="batched"))["valid"]
    await call(studio, "undo_edits", expected_revision=1)
    assert studio[0].state.dataset["samples"] == before
    await call(studio, "redo_edits", expected_revision=2)
    assert studio[0].state.dataset["samples"] == after


@pytest.mark.parametrize(
    "edits,options,status",
    [
        (
            [
                {
                    "name": "delete_event",
                    "arguments": {"sample_id": "test", "tick": "0", "event_index": 0},
                },
                {
                    "name": "create_event",
                    "arguments": {
                        "sample_id": "test",
                        "tick": "1",
                        "event": {
                            "op": "append",
                            "window": "a",
                            "source": "user",
                            "content": "bad",
                        },
                    },
                },
            ],
            {},
            422,
        ),
        (
            [
                {"name": "create_trajectory", "arguments": {"sample_id": "temporary"}},
                {"name": "delete_trajectory", "arguments": {"sample_id": "missing"}},
            ],
            {},
            404,
        ),
        ([{"name": "save_dataset", "arguments": {}}], {}, 422),
        ([{"name": "apply_batch", "arguments": {"edits": []}}], {}, 422),
        (
            [{"name": "create_trajectory", "arguments": {"expected_revision": 0}}],
            {},
            422,
        ),
        (
            [{"name": "create_trajectory", "arguments": {}}],
            {"expected_revision": 100},
            409,
        ),
        ([], {}, 422),
    ],
)
async def test_batch_failure_preserves_samples_revision_history(
    studio, edits, options, status
):
    await call(
        studio,
        "update_trajectory_metadata",
        sample_id="test",
        patch={"metadata": {"title": "before"}},
    )
    state = copy.deepcopy(studio[0].state.dataset)
    history = await call(studio, "get_edit_history", include_changes=True)
    response = await studio[1].post(
        "/api/mcp/call",
        json={"name": "apply_batch", "arguments": {"edits": edits, **options}},
    )
    assert response.status_code == status, response.text
    assert studio[0].state.dataset == state
    assert await call(studio, "get_edit_history", include_changes=True) == history


async def test_batch_leaves_untouched_invalid_samples_editable(studio):
    await call(studio, "create_trajectory", sample_id="invalid")
    await call(
        studio,
        "apply_batch",
        edits=[
            {
                "name": "rename_window",
                "arguments": {
                    "sample_id": "test",
                    "window": "a",
                    "new_window": "renamed",
                },
            }
        ],
    )
    assert not (await call(studio, "validate_trajectory", sample_id="invalid"))["valid"]


async def test_diffs_are_structured_paged_and_do_not_mutate(studio):
    await call(studio, "duplicate_trajectory", sample_id="test", new_sample_id="other")
    assert (
        await call(
            studio,
            "diff_trajectories",
            sample_id="test",
            other_sample_id="other",
            ignore_id=True,
        )
    )["equal"]
    before = (await call(studio, "get_trajectory", sample_id="test"))["sample"]
    candidate = copy.deepcopy(before)
    candidate["metadata"] = {"a/b~c": True, "text": "Hello 🙂"}
    candidate["replay"]["0"].reverse()
    result = await call(
        studio,
        "diff_trajectories",
        sample_id="test",
        other_sample=candidate,
        include_unified=True,
    )
    assert not result["equal"] and not result["unified_truncated"]
    assert "Hello 🙂" in result["unified_diff"]
    assert apply_changes(before, result["changes"]) == candidate
    page = await call(
        studio,
        "diff_trajectories",
        sample_id="test",
        other_sample=candidate,
        offset=1,
        limit=1,
    )
    assert page["changes"] == result["changes"][1:2]
    assert (await call(studio, "get_trajectory", sample_id="test"))["sample"] == before
    for arguments in ({}, {"other_sample_id": "other", "other_sample": candidate}):
        response = await studio[1].post(
            "/api/mcp/call",
            json={
                "name": "diff_trajectories",
                "arguments": {"sample_id": "test", **arguments},
            },
        )
        assert response.status_code == 422


async def test_history_restores_browser_edits_deletes_imports_and_preserves_config(
    studio,
):
    client = studio[1]
    original = copy.deepcopy(studio[0].state.dataset["samples"])
    await client.post("/api/samples", json={"sample": {**original[0], "id": "browser"}})
    await client.put(
        "/api/samples/0",
        json={"sample": {**original[0], "metadata": {"title": "browser edit"}}},
    )
    await client.delete("/api/samples/1")
    await client.post(
        "/api/import",
        json={
            "files": [
                {
                    "name": "sample.json",
                    "text": json.dumps({**original[0], "id": "imported"}),
                }
            ]
        },
    )
    final = copy.deepcopy(studio[0].state.dataset["samples"])
    config = copy.deepcopy(studio[0].state.dataset["config"])
    config["endpoints"][0]["api_key"] = "history-must-not-retain-this"
    response = await client.put("/api/config", json={"config": config})
    assert response.status_code == 200
    history = await call(studio, "get_edit_history", include_changes=True)
    assert len(history["undo"]) == 4
    assert "history-must-not-retain-this" not in json.dumps(history)
    revision = history["revision"]
    await call(studio, "undo_edits", steps=4, expected_revision=revision)
    assert studio[0].state.dataset["samples"] == original
    assert (
        studio[0].state.dataset["config"]["endpoints"][0]["api_key"]
        == "history-must-not-retain-this"
    )
    await call(studio, "redo_edits", steps=4)
    assert studio[0].state.dataset["samples"] == final
    await call(studio, "undo_edits")
    await call(
        studio,
        "update_trajectory_metadata",
        sample_id="test",
        patch={"metadata": {"new": True}},
    )
    assert not (await call(studio, "get_edit_history"))["can_redo"]


async def test_history_rejects_stale_busy_empty_and_excess_steps(studio):
    await call(studio, "create_trajectory", sample_id="one")
    for arguments, status in [
        ({"steps": 2}, 404),
        ({"expected_revision": 0}, 409),
        ({"steps": 0}, 422),
    ]:
        before = copy.deepcopy(studio[0].state.dataset)
        response = await studio[1].post(
            "/api/mcp/call", json={"name": "undo_edits", "arguments": arguments}
        )
        assert response.status_code == status
        assert studio[0].state.dataset == before
    studio[0].state.runner.status["running"] = True
    response = await studio[1].post("/api/mcp/call", json={"name": "undo_edits"})
    assert response.status_code == 409
    studio[0].state.runner.status["running"] = False
    await call(studio, "undo_edits")
    response = await studio[1].post("/api/mcp/call", json={"name": "undo_edits"})
    assert response.status_code == 404


async def test_history_will_not_overwrite_untracked_source_changes(studio):
    await call(studio, "create_trajectory", sample_id="one")
    source = studio[0].state.dataset["samples"][0]
    source["metadata"] = {"untracked": True}
    studio[0].state.dataset["samples"][0] = source
    before = copy.deepcopy(studio[0].state.dataset)
    response = await studio[1].post("/api/mcp/call", json={"name": "undo_edits"})
    assert response.status_code == 409
    assert studio[0].state.dataset == before


def test_history_evicts_old_or_oversized_edits_and_keeps_redo_values_isolated():
    state = {"samples": [], "revision": 0}
    history = SampleHistory(state, max_entries=2, max_bytes=10_000)
    history.commit([{"id": "a"}], "one")
    history.commit([{"id": "a", "nested": {"value": 1}}], "two")
    history.commit([{"id": "a", "nested": {"value": 2}}], "three")
    assert len(history.view()["undo"]) == 2 and history.view()["notice"]
    history.restore("undo", 2)
    assert state["samples"] == [{"id": "a"}]
    history.restore("redo", 2)
    state["samples"][0]["nested"]["value"] = 99
    assert history.undo_stack[-1]["forward"][-1]["value"] == 2
    history.commit([{"id": "huge", "content": "x" * 20_000}], "huge")
    assert not history.view()["can_undo"] and history.bytes_used() <= history.max_bytes


def test_json_patch_paths_lists_and_json_types():
    before = {"a/b~": [1, {"enabled": False}, 3], "": {"old": None}}
    after = {"a/b~": [0, 1, {"enabled": 0}, 3], "": {"new": True}}
    changes = json_changes(before, after)
    assert any(p["path"].startswith("/a~1b~0/") for p in changes)
    assert apply_changes(before, changes) == after
    assert json_changes({"value": False}, {"value": 0}) == [
        {"op": "replace", "path": "/value", "value": 0}
    ]


def test_json_patch_roundtrip_for_varied_json_trees():
    rng = random.Random(41)

    def tree(depth):
        if depth == 0:
            return rng.choice([None, False, 0, 1.0, "🙂/~", ""])
        kind = rng.randrange(3)
        if kind == 0:
            return [tree(depth - 1) for _ in range(rng.randrange(5))]
        if kind == 1:
            return {
                str(rng.randrange(5)): tree(depth - 1) for _ in range(rng.randrange(4))
            }
        return tree(0)

    for _ in range(150):
        before, after = tree(3), tree(3)
        assert json.dumps(
            apply_changes(before, json_changes(before, after)), sort_keys=True
        ) == json.dumps(after, sort_keys=True)
