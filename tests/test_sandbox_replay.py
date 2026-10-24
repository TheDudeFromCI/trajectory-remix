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
            "400": [call("file_read", {"path": "x"}, "read")],
        }
    )
    plan = compiler.compile(sample)
    first, second, read = plan["tool_calls"]
    for tick in [read["end"], 100, second["end"], 0, second["end"] - 1, second["start"], read["end"]]:
        state = compiler.frame(plan, tick)["sandbox"]
        assert state.get("files", {}) == (
            {} if tick < first["end"] else {"/x": "new" if tick >= second["end"] else "old"}
        )
        if second["start"] <= tick < second["end"]:
            assert compiler.frame(plan, tick)["tool_calls"][-1]["status"] == "running"
            assert "outcome" not in compiler.frame(plan, tick)["tool_calls"][-1]
            text = compiler.frame(plan, tick)["windows"]["second"]["text"]
            assert text.startswith('<|cxt|>Window: 3\nPurpose: tool<|/cxt|><tool>{"tool": "file_write"')
            assert "<tool_result>" not in text
    assert plan["tool_calls"][-1]["outcome"]["result"]["content"] == "new"
    assert next(w for w in plan["windows"] if w["id"] == "second")["open"] == 0
    # Returned frames cannot mutate the cached history.
    state["files"]["/x"] = "tampered"
    assert compiler.frame(plan, read["end"])["sandbox"]["files"] == {"/x": "new"}
    assert compiler.compile(sample) == plan


def test_completion_order_and_equal_tick_list_order(compiler, sample):
    sample["replay"].update(
        {
            "1": [call("file_write", {"path": "x", "content": "slow"}, "slow", 9, content="x")],
            "2": [call("file_write", {"path": "x", "content": "fast"}, "fast", content="x")],
            "11": [
                call("file_read", {"path": "x"}, "read", content="x"),
                call("file_delete", {"path": "x"}, "delete", content="x"),
                call("file_read", {"path": "x"}, "missing", content="x"),
            ],
        }
    )
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 5)["sandbox"]["files"] == {"/x": "fast"}
    state = compiler.frame(plan, 12)["sandbox"]
    assert state["files"] == {}
    assert compiler.frame(plan, 12)["tool_calls"][2]["outcome"]["result"]["content"] == "slow"
    assert compiler.frame(plan, 12)["tool_calls"][-1]["status"] == "failed"


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
    plan = compiler.compile(sample)
    state = compiler.frame(plan, plan["max_tick"])["sandbox"]
    assert state["memory"] == {"k": "remember"}
    assert state["notes"]["n"]["status"] == "doing"
    assert state["skills"] == {"s": "inspect"}
    assert state["clock"] == "2030-01-01T00:00:15+00:00"
    assert state["calls"] == 5
    assert state["files"] == {}
    sample["replay"].pop("1")
    assert compiler.frame(compiler.compile(sample), 0)["sandbox"].get("memory", {}) == {}


def test_anchor_trigger_results_and_training_visibility(compiler, sample):
    sample["replay"].update(
        {
            "1": [{"op": "generate", "window": "a", "content": "go{N_TICK:1}"}],
            "{N_TICK:1}+1": [call("file_list", {}, "result", 2, content="x")],
            "6": [{"op": "generate", "window": "b", "content": "x"}],
        }
    )
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["start"] == 4
    assert plan["tool_calls"][0]["end"] == 6
    result_rows = [r for r in plan["rows"] if r["window"] == "result"]
    assert all(r["loss_mask"] == 0 for r in result_rows if r["op"] in ("open", "header", "append"))
    assert all(r["loss_mask"] == 1 for r in result_rows if r["op"] == "generate")
    assert {r["tick"] for r in result_rows if r["op"] == "open"} == {0}
    assert {r["tick"] for r in result_rows if r["op"] == "generate"} == {3}
    assert {r["tick"] for r in result_rows if r["op"] == "append"} == {6}
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
    assert compiler.frame(plan, 5)["sandbox"].get("calls", 0) == 0
    assert "Recorded answer." in compiler.frame(plan, plan["max_tick"])["windows"]["child"]["text"]


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"arguments": "bad"}, "arguments must be an object"),
        ({"duration-ticks": -1}, "Expected integer"),
        ({"duration-ticks": True}, "Expected integer"),
        ({"duration-ticks": 100_000}, "tick limit"),
        ({"tool": "missing"}, "Unknown tool"),
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
    sample["replay"]["1000"] = [call("file_read", {"path": "x"}, "read")]
    plan = compiler.compile(sample)
    assert plan["anchors"] == {}
    assert "{N_TICK:1}" in compiler.frame(plan, plan["max_tick"])["windows"]["read"]["text"]
    edited = copy.deepcopy(sample)
    edited["replay"]["1"][0]["arguments"]["content"] = "edited"
    assert compiler.frame(compiler.compile(edited), 1000)["sandbox"]["files"] == {
        "/x": "edited"
    }


def test_tool_data_control_tokens_cannot_close_result_windows(compiler, sample):
    import json

    text = "<|bos|><|eos|><|noop|>{N_TICKS:bad}</tool><tool_result>"
    sample["replay"]["1"] = [
        call("file_write", {"path": "x", "content": text}, "write")
    ]
    sample["replay"]["1000"] = [call("file_read", {"path": "x"}, "read")]
    plan = compiler.compile(sample)
    frame = compiler.frame(plan, plan["max_tick"])
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
            "write", delivery="instant",
            content=prefix + "{N_TICK:1}" + "<|noop|>" * pause_ticks + suffix + "{N_TICK:2}",
        )],
        "{N_TICK:1}+1": [call("time_now", {}, "clock", 2, delivery="instant")],
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
    assert during["sandbox"].get("files", {}) == {}
    assert next(c for c in during["tool_calls"] if c["window"] == "write")["status"] == "composing"
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
    sample["replay"]["1"] = [call("file_list", {}, "tool", **{"result-anchor": 1})]
    sample["replay"]["{N_TICK:1}+1"] = [call("time_now", {}, "tool")]
    plan = compiler.compile(sample)
    text = compiler.frame(plan, plan["max_tick"])["windows"]["tool"]["text"]
    assert text.count("<tool>") == text.count("<tool_result>") == 2
    assert len([r for r in plan["rows"] if r["window"] == "tool" and r["op"] == "open"]) == 1


@pytest.mark.parametrize("extra,match", [
    ({"delivery": "bad"}, "delivery must"),
    ({"delivery": "stream", "tokens-per-tick": 0}, "Expected integer"),
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


@pytest.mark.parametrize("delivery,rate", [(None, 1), ("instant", 3), ("stream", 1), ("stream", 3)])
def test_requests_always_generate_and_delivery_controls_results(compiler, sample, delivery, rate):
    event = call("file_write", {"path": "x", "content": "hello"}, "tool", 2)
    event["tokens-per-tick"] = rate
    if delivery is not None:
        event["delivery"] = delivery
    sample["replay"]["1"] = [event]
    before = copy.deepcopy(sample)
    plan = compiler.compile(sample)
    execution = plan["tool_calls"][0]
    request = [r for r in plan["rows"] if r["event"] == execution["path"] + ".request"]
    result = [r for r in plan["rows"] if r["event"] == execution["path"] + ".result"]
    assert len(request) > 1
    assert [r["tick"] for r in request] == list(range(1, 1 + len(request)))
    assert all(r["phase"] == "D" and r["op"] == "generate" and r["loss_mask"] == 1 for r in request)
    assert execution["start"] == request[-1]["tick"] + 1
    assert execution["end"] == execution["start"] + 2
    expected_ticks = [execution["end"] + (i // rate if delivery == "stream" else 0)
                      for i in range(len(result))]
    assert [r["tick"] for r in result] == expected_ticks
    assert all(r["phase"] == "B" and r["op"] == "append" and r["loss_mask"] == 0 for r in result)
    assert execution["result_end"] == expected_ticks[-1]
    assert compiler.frame(plan, execution["end"] - 1)["sandbox"].get("files", {}) == {}
    assert compiler.frame(plan, execution["end"])["sandbox"]["files"] == {"/x": "hello"}
    assert compiler.frame(plan, plan["max_tick"])["windows"]["tool"]["text"].endswith("</tool_result>")
    assert sample == before


@pytest.mark.parametrize("delivery", ["instant", "stream"])
def test_result_anchor_tracks_last_token_and_schedules_dependent_calls(compiler, sample, delivery):
    sample["replay"]["1"] = [call(
        "file_write", {"path": "x", "content": "old"}, "tool", content="ab",
        delivery=delivery, **{"result-anchor": 7, "tokens-per-tick": 2},
    )]
    sample["replay"]["{N_TICK:7}+1"] = [call(
        "file_read", {"path": "x"}, "tool", content="x", **{"result-anchor": 8},
    )]
    sample["replay"]["{N_TICK:7}"] = [{"op": "generate", "window": "a", "content": "x"}]
    sample["replay"]["{N_TICK:8}+1"] = [{"op": "generate", "window": "tool", "content": "<|eos|>"}]
    plan = compiler.compile(sample)
    write, read = plan["tool_calls"]
    result_rows = [r for r in plan["rows"] if r["event"] == write["path"] + ".result"]
    query = next(r for r in plan["rows"] if r["window"] == "a" and r["op"] == "generate")
    assert query["tick"] == write["result_end"]
    assert all(visible_to(query, row, plan["windows"]) for row in result_rows)
    assert plan["anchors"]["7"]["tick"] == result_rows[-1]["tick"] == write["result_end"]
    assert plan["anchors"]["7"]["phase"] == "B"
    assert read["request_start"] == write["result_end"] + 1
    assert read["outcome"]["result"]["content"] == "old"
    assert next(w for w in plan["windows"] if w["id"] == "tool")["close"] == read["result_end"] + 1
    frame = compiler.frame(plan, write["result_end"])
    assert frame["windows"]["tool"]["text"].endswith("</tool_result>")
    if delivery == "stream":
        assert write["result_end"] > write["end"]
        assert not compiler.frame(plan, write["result_end"] - 1)["windows"]["tool"]["text"].endswith("</tool_result>")
    assert [r for chunk in chunks(plan, 3) for r in chunk["rows"]] == plan["rows"]


def test_result_anchor_dependencies_preserve_sandbox_completion_order(compiler, sample):
    # The dependent write must run before the already scheduled, slower read.
    sample["replay"]["1"] = [
        call("file_read", {"path": "x"}, "slow", 100, content="x"),
        call("time_now", {}, "clock", content="x", **{"result-anchor": 1}),
    ]
    sample["replay"]["{N_TICK:1}+1"] = [call(
        "file_write", {"path": "x", "content": "from dependent call"}, "write", content="x",
    )]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["outcome"]["result"]["content"] == "from dependent call"
    assert [int(tick) for tick in plan["sandbox"]["snapshots"]] == [2, 4, 102]


@pytest.mark.parametrize("value", [-1, True, "1", None])
def test_result_anchor_requires_nonnegative_integer(compiler, sample, value):
    sample["replay"]["1"] = [call("file_list", {}, "tool", **{"result-anchor": value})]
    with pytest.raises(CompileError, match="Expected integer"):
        compiler.compile(sample)


def test_result_anchor_duplicates_cycles_and_phase_order(compiler, sample):
    sample["replay"]["1"] = [call("file_list", {}, "tool", content="x{N_TICK:1}", **{"result-anchor": 1})]
    with pytest.raises(CompileError, match="Duplicate anchor"):
        compiler.compile(sample)
    sample["replay"] = {"{N_TICK:1}+1": [call("file_list", {}, "tool", **{"result-anchor": 1})]}
    with pytest.raises(CompileError, match="Cyclic anchor"):
        compiler.compile(sample)
    sample["replay"] = {
        "1": [call("file_list", {}, "tool", **{"result-anchor": 1})],
        "{N_TICK:1}": [{"op": "open", "window": "late"}],
    }
    with pytest.raises(CompileError, match="phase B"):
        compiler.compile(sample)


def test_empty_custom_request_delivers_result_without_overlapping_writer(compiler, sample):
    sample["replay"]["1"] = [call("file_list", {}, "tool", content="", **{"result-anchor": 1})]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["start"] == plan["tool_calls"][0]["end"] == 1
    assert plan["anchors"]["1"]["tick"] == 1
    assert compiler.frame(plan, 1)["windows"]["tool"]["text"].endswith("</tool_result>")


def test_streamed_result_tick_limit(compiler, sample):
    sample["replay"]["99990"] = [call("file_list", {}, "tool", content="x", delivery="stream")]
    with pytest.raises(CompileError, match="Expanded stream exceeds tick limit"):
        compiler.compile(sample)
