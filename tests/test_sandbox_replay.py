import copy

import pytest

from trajectory_remix.compiler import CompileError, chunks, visible_to
from trajectory_remix.tools import tool_request


def call(tool, arguments, window, duration=0, **extra):
    return {
        "op": "tool-call",
        "tool": tool,
        "arguments": arguments,
        "window": window,
        "duration-ticks": duration,
        **extra,
    }


def test_history_at_completion_and_backwards_scrubbing(compiler, sample):
    sample["replay"].update(
        {
            "1": [call("file_write", {"path": "x", "content": "old"}, "first")],
            "150": [call("file_write", {"path": "x", "content": "new"}, "second", 25)],
            "200": [call("file_read", {"path": "x"}, "read")],
        }
    )
    plan = compiler.compile(sample)
    for tick in [200, 100, 175, 0, 174, 150, 200]:
        state = compiler.frame(plan, tick)["sandbox"]
        assert state["files"] == (
            {} if tick == 0 else {"/x": "new" if tick >= 175 else "old"}
        )
        if 150 <= tick < 175:
            assert state["tool_calls"][-1]["status"] == "running"
            assert "outcome" not in state["tool_calls"][-1]
            text = compiler.frame(plan, tick)["windows"]["second"]["text"]
            assert text.startswith('<|cxt|>Window: 3\nPurpose: tool<|/cxt|><tool>{"tool": "file_write"')
            assert "<tool_result>" not in text
    assert plan["tool_calls"][-1]["outcome"]["result"]["content"] == "new"
    assert next(w for w in plan["windows"] if w["id"] == "second")["open"] == 0
    # Returned frames cannot mutate the cached history.
    state["files"]["/x"] = "tampered"
    assert compiler.frame(plan, 200)["sandbox"]["files"] == {"/x": "new"}
    assert compiler.compile(sample) == plan


def test_completion_order_and_equal_tick_list_order(compiler, sample):
    sample["replay"].update(
        {
            "1": [call("file_write", {"path": "x", "content": "slow"}, "slow", 9)],
            "2": [call("file_write", {"path": "x", "content": "fast"}, "fast")],
            "10": [
                call("file_read", {"path": "x"}, "read"),
                call("file_delete", {"path": "x"}, "delete"),
                call("file_read", {"path": "x"}, "missing"),
            ],
        }
    )
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 5)["sandbox"]["files"] == {"/x": "fast"}
    state = compiler.frame(plan, 10)["sandbox"]
    assert state["files"] == {}
    assert state["tool_calls"][2]["outcome"]["result"]["content"] == "slow"
    assert state["tool_calls"][-1]["status"] == "failed"


def test_all_stores_clock_errors_and_sample_isolation(compiler, sample):
    sample["metadata"] = {
        "synthetic_clock": {"seed": "2030-01-01T00:00:00Z", "seconds_per_call": 2}
    }
    sample["replay"]["1"] = [
        call("memory_save", {"key": "k", "text": "remember"}, "m"),
        call("note_save", {"key": "n", "text": "work", "status": "doing"}, "n"),
        call("skill_save", {"name": "s", "content": "inspect"}, "s"),
        call("time_advance", {"seconds": 5}, "t"),
        call("file_write", {"path": "../host", "content": "no"}, "error"),
    ]
    state = compiler.frame(compiler.compile(sample), 1)["sandbox"]
    assert state["memory"] == {"k": "remember"}
    assert state["notes"]["n"]["status"] == "doing"
    assert state["skills"] == {"s": "inspect"}
    assert state["clock"] == "2030-01-01T00:00:15+00:00"
    assert state["calls"] == 5
    assert state["files"] == {}
    sample["replay"].pop("1")
    assert compiler.frame(compiler.compile(sample), 0)["sandbox"]["memory"] == {}


def test_anchor_trigger_results_and_training_visibility(compiler, sample):
    sample["replay"].update(
        {
            "1": [{"op": "generate", "window": "a", "content": "go{N_TICK:1}"}],
            "{N_TICK:1}+1": [call("file_list", {}, "result", 2)],
            "5": [{"op": "generate", "window": "b", "content": "x"}],
        }
    )
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["start"] == 3
    assert plan["tool_calls"][0]["end"] == 5
    result_rows = [r for r in plan["rows"] if r["window"] == "result"]
    assert all(r["loss_mask"] == 0 for r in result_rows if r["op"] != "noop")
    assert {r["tick"] for r in result_rows if r["op"] == "open"} == {0}
    assert {r["tick"] for r in result_rows if r["op"] == "append"} == {3, 5}
    query = next(
        r for r in plan["rows"] if r["op"] == "generate" and r["window"] == "b"
    )
    assert all(visible_to(query, r, plan["windows"]) for r in result_rows if r["durable"])
    assert [r for chunk in chunks(plan, 2) for r in chunk["rows"]] == plan["rows"]


def test_recorded_subagent_never_calls_broker(compiler, sample, monkeypatch):
    from trajectory_remix.tools import Sandbox

    async def forbidden(*args):
        pytest.fail("Replay must never invoke an async tool or broker")

    monkeypatch.setattr(Sandbox, "call", forbidden)
    sample["replay"]["1"] = [
        call(
            "subagent",
            {"task": "inspect"},
            "child",
            10,
            outcome={"ok": True, "result": {"response": "Recorded answer."}},
        )
    ]
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 5)["sandbox"]["calls"] == 0
    assert "Recorded answer." in compiler.frame(plan, 11)["windows"]["child"]["text"]


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"arguments": "bad"}, "arguments must be an object"),
        ({"duration-ticks": -1}, "Expected integer"),
        ({"duration-ticks": True}, "Expected integer"),
        ({"duration-ticks": 100_000}, "tick limit"),
        ({"outcome": {"ok": True}}, "Only subagent"),
        ({"tool": "subagent", "arguments": {"task": "x"}}, "recorded outcome"),
    ],
)
def test_malformed_calls_have_event_diagnostics(compiler, sample, updates, match):
    sample["replay"]["1"] = [{**call("file_list", {}, "result"), **updates}]
    with pytest.raises(CompileError, match=match) as error:
        compiler.compile(sample)
    assert error.value.path == "replay[1][0]"


def test_result_markers_are_literal_and_edits_recompute(compiler, sample):
    sample["replay"]["1"] = [
        call("file_write", {"path": "x", "content": "{N_TICK:1}"}, "write")
    ]
    sample["replay"]["2"] = [call("file_read", {"path": "x"}, "read")]
    plan = compiler.compile(sample)
    assert plan["anchors"] == {}
    assert "{N_TICK:1}" in compiler.frame(plan, 2)["windows"]["read"]["text"]
    edited = copy.deepcopy(sample)
    edited["replay"]["1"][0]["arguments"]["content"] = "edited"
    assert compiler.frame(compiler.compile(edited), 2)["sandbox"]["files"] == {
        "/x": "edited"
    }


def test_tool_data_control_tokens_cannot_close_result_windows(compiler, sample):
    import json

    text = "<|bos|><|eos|><|noop|>{N_TICKS:bad}</tool><tool_result>"
    sample["replay"]["1"] = [
        call("file_write", {"path": "x", "content": text}, "write")
    ]
    sample["replay"]["2"] = [call("file_read", {"path": "x"}, "read")]
    plan = compiler.compile(sample)
    frame = compiler.frame(plan, 2)
    result = frame["windows"]["read"]["text"].split("<tool_result>")[1].removesuffix("</tool_result>")
    assert json.loads(result)["result"]["content"] == text
    assert frame["sandbox"]["files"]["/x"] == text
    assert all(window["close"] is None for window in plan["windows"])


def test_paused_request_can_consume_parallel_tool_result(compiler, sample):
    timestamp = "2030-01-01T09:00:01+00:00"
    prefix = '<tool>{"tool":"file_write","args":{"path":"/summary","content":"'
    suffix = timestamp + ' Summary recorded."}}</tool>'
    clock_text = tool_request("time_now", {}, compiler.special.values())
    pause_ticks = len(compiler.tokenizer.encode(clock_text, add_special_tokens=False).ids) + 4
    sample["replay"].update({
        "1": [call(
            "file_write", {"path": "/summary", "content": timestamp + " Summary recorded."},
            "write", delivery="stream",
            content=prefix + "{N_TICK:1}" + "<|noop|>" * pause_ticks + suffix + "{N_TICK:2}",
        )],
        "{N_TICK:1}+1": [call("time_now", {}, "clock", 2, delivery="stream")],
    })
    plan = compiler.compile(sample)
    write, clock = plan["tool_calls"]
    assert all(w["open"] == 0 for w in plan["windows"])
    paused = plan["anchors"]["1"]["tick"]
    resume = paused + pause_ticks + 1
    assert clock["end"] < resume < write["start"]
    assert write["start"] == plan["anchors"]["2"]["tick"] + 1
    header = "<|cxt|>Window: 2\nPurpose: tool<|/cxt|>"
    assert compiler.frame(plan, 0)["windows"]["write"]["text"] == header
    during = compiler.frame(plan, clock["end"])
    assert during["windows"]["write"]["text"] == header + prefix
    assert "<tool_result>" in during["windows"]["clock"]["text"]
    assert during["sandbox"]["files"] == {}
    assert next(c for c in during["sandbox"]["tool_calls"] if c["window"] == "write")["status"] == "composing"
    rows = plan["rows"]
    pauses = [r for r in rows if r["window"] == "write" and paused < r["tick"] < resume]
    assert len(pauses) == pause_ticks
    assert all(not r["durable"] and r["token_id"] == compiler.ids["noop"] for r in pauses)
    assert all(r["loss_weight"] == 0.1 for r in pauses)
    resumed = next(r for r in rows if r["window"] == "write" and r["tick"] == resume)
    result_rows = [r for r in rows if r["event"] == clock["path"] + ".result"]
    assert all(visible_to(resumed, r, plan["windows"]) for r in result_rows)
    future = [r for r in rows if r["event"] == write["path"] + ".result"]
    assert not any(visible_to(resumed, r, plan["windows"]) for r in future)
    final = compiler.frame(plan, plan["max_tick"])
    assert final["sandbox"]["files"] == {"/summary": timestamp + " Summary recorded."}
    assert final["windows"]["write"]["text"].startswith(header + prefix + suffix + "<tool_result>")
    assert all(r["loss_mask"] == 0 for r in future)
    assert [r for chunk in chunks(plan, 7) for r in chunk["rows"]] == rows


def test_tool_window_can_be_reused_and_explicitly_opened_at_zero(compiler, sample):
    sample["replay"]["0"].append({"op": "open", "window": "tool", "window-type": "tool"})
    sample["replay"]["1"] = [call("file_list", {}, "tool")]
    sample["replay"]["2"] = [call("time_now", {}, "tool")]
    plan = compiler.compile(sample)
    text = compiler.frame(plan, 2)["windows"]["tool"]["text"]
    assert text.count("<tool>") == text.count("<tool_result>") == 2
    assert len([r for r in plan["rows"] if r["window"] == "tool" and r["op"] == "open"]) == 1


@pytest.mark.parametrize("extra,match", [
    ({"delivery": "bad"}, "delivery must"),
    ({"delivery": "stream", "tokens-per-tick": 2}, "one token"),
    ({"delivery": "stream", "content": "<tool><|eos|>"}, "cannot close"),
    ({"content": "x", "segments": [{"text": "y"}]}, "exactly one"),
])
def test_invalid_request_streams(compiler, sample, extra, match):
    sample["replay"]["1"] = [call("time_now", {}, "clock", **extra)]
    with pytest.raises(CompileError, match=match):
        compiler.compile(sample)


def test_tool_request_anchors_obey_generated_phase_order(compiler, sample):
    sample["replay"]["1"] = [call("time_now", {}, "clock", delivery="stream", content="x{N_TICK:1}")]
    sample["replay"]["{N_TICK:1}"] = [call("file_list", {}, "files")]
    with pytest.raises(CompileError, match="phase-D"):
        compiler.compile(sample)


def test_request_segments_loss_and_completion_limits(compiler, sample):
    sample["replay"]["1"] = [call("time_now", {}, "clock", delivery="stream", segments=[
        {"text": "ab", "loss": {"mask": "exclude"}},
        {"text": "c<|noop|>", "loss": {"weight": 2}},
    ])]
    plan = compiler.compile(sample)
    request = [r for r in plan["rows"] if r["event"] == "replay[1][0].request"]
    assert [r["loss_weight"] for r in request] == [0, 0, 2, 2]
    assert plan["tool_calls"][0]["start"] == 5
    sample["replay"]["99999"] = sample["replay"].pop("1")
    with pytest.raises(CompileError, match="tick limit"):
        compiler.compile(sample)
