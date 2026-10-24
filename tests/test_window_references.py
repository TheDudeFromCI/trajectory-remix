import copy

import pytest

from trajectory_remix.compiler import CompileError, chunks
from trajectory_remix.training import query_batch


def test_named_references_and_headers_share_exported_numeric_slots(compiler, sample):
    sample["replay"]["0"] = [
        {"op": "open", "window": name, "window-type": purpose}
        for name, purpose in [("system", "system"), ("user", "chat"),
                              ("reasoning", "reasoning"), ("chat", "chat"), ("tool0", "tool")]
    ]
    sample["replay"]["1"] = [{
        "op": "generate", "window": "reasoning",
        "content": "I'll read the file in Window {WINDOW:tool0}.{N_TICK:1}",
    }]
    before = copy.deepcopy(sample)
    plan = compiler.compile(sample)
    assert plan["window_slots"]["tool0"] == 4
    assert compiler.frame(plan, plan["max_tick"])["windows"]["reasoning"]["text"] == (
        "<|cxt|>Window: 2\nPurpose: reasoning<|/cxt|>I'll read the file in Window 4."
    )
    assert compiler.frame(plan, 0)["windows"]["tool0"]["text"] == (
        "<|cxt|>Window: 4\nPurpose: tool<|/cxt|>"
    )
    assert plan["anchors"]["1"]["tick"] == len("I'll read the file in Window 4.")
    exported = list(chunks(plan, 3))
    assert all(c["window_slots"] == plan["window_slots"] for c in exported)
    assert sample == before
    other = copy.deepcopy(sample)
    other["replay"] = dict(reversed(list(other["replay"].items())))
    assert compiler.compile(other) == plan


def test_multi_digit_reference_resolution_precedes_tokenization_and_segment_masks(compiler, sample):
    sample["replay"]["0"] = [{"op": "open", "window": f"w{i}"} for i in range(12)]
    sample["replay"]["1"] = [{
        "op": "generate", "window": "w0", "segments": [
            {"text": "{WINDOW:w11}{N_TICK:1}", "loss": {"mask": "exclude"}},
            {"text": " {WINDOW:w10}", "loss": {"mask": "include", "weight": 0.5}},
        ],
    }]
    plan = compiler.compile(sample)
    rows = [r for r in plan["rows"] if r["op"] == "generate"]
    assert compiler.tokenizer.decode([r["token_id"] for r in rows]) == "11 10"
    assert [r["loss_weight"] for r in rows] == [0, 0, 0.5, 0.5, 0.5]
    assert plan["anchors"]["1"]["tick"] == 2


def test_forward_reference_to_implicit_tool_and_training_slots_agree(compiler, sample):
    sample["replay"]["0"].append({
        "op": "generate", "window": "a", "content": "{WINDOW:tool0}",
    })
    sample["replay"]["2"] = [{
        "op": "tool-call", "window": "tool0", "tool": "file_list", "arguments": {},
    }]
    plan = compiler.compile(sample)
    # Implicit tool windows are replayed first but retain their declared slot.
    assert plan["windows"][0]["id"] == "tool0"
    assert plan["windows"][0]["slot"] == 2
    assert next(e for e in plan["events"] if e["op"] == "generate")["text"] == "2"
    batch = query_batch(plan, plan["rows"])
    memory = [r for r in plan["rows"] if r["durable"]]
    targets = [r for r in plan["rows"] if r["loss_weight"] > 0]
    assert batch["memory_window_ids"] == [plan["window_slots"][r["window"]] for r in memory]
    assert batch["query_window_ids"] == [plan["window_slots"][r["window"]] for r in targets]


@pytest.mark.parametrize("content,match", [
    ("{WINDOW:missing}", "Unknown window reference"),
    ("{WINDOW:}", "Invalid window marker"),
    ("{WINDOW:a", "Invalid window marker"),
    ("{WINDOWS:a}", "Invalid window marker"),
])
def test_invalid_references_are_diagnostics(compiler, sample, content, match):
    sample["replay"]["1"] = [{"op": "generate", "window": "a", "content": content}]
    with pytest.raises(CompileError, match=match) as error:
        compiler.compile(sample)
    assert error.value.path == "replay[1][0]"


def test_escaped_references_and_tool_arguments_are_literal(compiler, sample):
    sample["replay"]["1"] = [{
        "op": "append", "window": "a", "source": "user", "content": r"\{WINDOW:missing}",
    }, {
        "op": "tool-call", "window": "tool0", "tool": "file_write",
        "arguments": {"path": "x", "content": "{WINDOW:a}{WINDOW:missing}"},
    }]
    plan = compiler.compile(sample)
    assert next(e for e in plan["events"] if e["window"] == "a" and e["op"] == "append")["text"] == "{WINDOW:missing}"
    result = plan["tool_calls"][0]
    assert compiler.frame(plan, result["end"])["sandbox"]["files"]["/x"] == "{WINDOW:a}{WINDOW:missing}"
    assert "{WINDOW:missing}" in next(e for e in plan["events"] if e["path"] == result["path"] + ".request")["text"]


async def test_rename_updates_only_structured_references_and_is_undoable(studio):
    app, client = studio
    source = app.state.dataset["samples"][0]
    source["replay"]["1"] = [{
        "op": "generate", "window": "b", "segments": [
            {"text": "a {WINDOW:a} "}, {"text": r"\{WINDOW:a} {WINDOW:b}"},
        ],
    }]
    app.state.dataset["samples"][0] = source
    before = copy.deepcopy(app.state.dataset["samples"][0])
    response = await client.post("/api/mcp/call", json={"name": "rename_window", "arguments": {
        "sample_id": "test", "window": "a", "new_window": "renamed", "validate": True,
    }})
    assert response.status_code == 200, response.text
    parts = app.state.dataset["samples"][0]["replay"]["1"][0]["segments"]
    assert parts[0]["text"] == "a {WINDOW:renamed} "
    assert parts[1]["text"] == r"\{WINDOW:a} {WINDOW:b}"
    response = await client.post("/api/mcp/call", json={"name": "undo_edits", "arguments": {}})
    assert response.status_code == 200, response.text
    assert app.state.dataset["samples"][0] == before
