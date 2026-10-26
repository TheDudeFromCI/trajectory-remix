import pytest

from trajectory_remix.compiler import Compiler
from trajectory_remix.sandbox_replay import tool_calls_frame
from trajectory_remix.tools import ToolRegistry
from test_io_extensions import call, hidden


def eos(window, content="<|eos|>"):
    return {"op": "generate", "window": window, "content": content}


def test_eos_abandons_partial_tool_args_without_dispatch(compiler, sample):
    sample["replay"]["1"] = [
        call(
            "file_write",
            {},
            content='{"path": "unfinished<|eos|>',
            **{"result-anchor": 7},
        )
    ]
    plan = compiler.compile(sample)
    item = plan["tool_calls"][0]
    assert item["cancelled"] and item["end"] < item["start"]
    assert (
        next(window for window in plan["windows"] if window["id"] == "tool")["close"]
        == item["end"]
    )
    assert tool_calls_frame(plan, item["end"])[0]["status"] == "cancelled"
    assert plan["anchors"]["7"]["tick"] == item["end"]
    assert plan["sandbox"]["snapshots"] == {}
    assert not any(".result" in row["event"] for row in plan["rows"])


@pytest.mark.parametrize(
    "tool,args,key",
    [
        ("timer", {"mode": "timeout", "seconds": 100}, "timers"),
        ("timer", {"mode": "interval", "seconds": 0.1, "count": 100}, "timers"),
        ("file_watch", {"path": "x", "timeout_seconds": 100}, "file_watchers"),
        ("web_open", {"url": "https://example.test/api"}, "web_pending"),
        ("web_search", {"query": "example"}, "web_pending"),
        ("web_find", {"url": "https://example.test/api", "text": "example"}, "web_pending"),
    ],
)
def test_eos_cleans_pending_state_and_preserves_other_active_calls(
    compiler, sample, tool, args, key
):
    sample["replay"]["0"].append(
        hidden("web_set", {"pages": [{"url": "https://example.test/api",
               "title": "Example", "text": "example", "latency_seconds": 20}],
               "search_latency_seconds": 20})
    )
    sample["replay"]["1"] = [
        call(tool, args, "cancel"),
        call("timer", {"mode": "timeout", "ticks": 8}, "finish"),
    ]
    sample["replay"]["4"] = [eos("cancel")]
    plan = compiler.compile(sample)
    cancelled, completed = plan["tool_calls"][1:]
    assert cancelled["end"] == 4 and cancelled["cancelled"]
    assert completed["end"] == 10 and completed["outcome"]["ok"]
    assert key not in compiler.frame(plan, 4)["sandbox"]
    assert all(out["tick"] <= 4 for out in cancelled["outputs"])
    assert tool_calls_frame(plan, 3)[1]["status"] == "running"
    assert tool_calls_frame(plan, 4)[1]["status"] == "cancelled"


def test_cancel_delayed_file_write_never_commits(compiler, sample):
    sample["replay"]["0"].append(hidden("hardware_load_set", {"file_io_ticks": 20}))
    sample["replay"]["1"] = [
        call("file_write", {"path": "x", "content": "never written"})
    ]
    sample["replay"]["5"] = [eos("tool")]
    plan = compiler.compile(sample)
    state = compiler.frame(plan, plan["max_tick"])["sandbox"]
    assert not state.get("files") and "file_io_pending" not in state
    assert plan["tool_calls"][1]["cancelled"]


def test_cancel_subagent_releases_running_conversation(compiler, sample):
    sample["replay"]["0"].append(
        hidden("subagent", {"task": "task", "response": "later"})
    )
    sample["replay"]["1"] = [
        call("subagent", {"task": "task"}, **{"duration-ticks": 20})
    ]
    sample["replay"]["5"] = [eos("tool")]
    plan = compiler.compile(sample)
    state = compiler.frame(plan, 5)["sandbox"]
    assert "subagent_runs" not in state
    assert state["conversations"]["conversation-1"]["status"] == "cancelled"
    assert len(state["conversations"]["conversation-1"]["messages"]) == 1


def test_completion_on_eos_tick_commits_phase_b_result(compiler, sample):
    sample["replay"]["1"] = [
        call("file_write", {"path": "x", "content": "saved"}, **{"duration-ticks": 2})
    ]
    sample["replay"]["4"] = [eos("tool")]
    plan = compiler.compile(sample)
    item = plan["tool_calls"][0]
    assert item["end"] == 4 and not item.get("cancelled")
    assert compiler.frame(plan, 4)["sandbox"]["files"] == {"/x": "saved"}


def test_eos_cuts_off_streaming_result_after_close(compiler, sample):
    sample["replay"]["1"] = [call("time_now", {}, delivery="stream")]
    sample["replay"]["4"] = [eos("tool")]
    plan = compiler.compile(sample)
    results = [row for row in plan["rows"] if ".result" in row["event"]]
    assert results and max(row["tick"] for row in results) == 4
    assert plan["tool_calls"][0]["end"] == 2
    result_event = next(event for event in plan["events"] if ".result" in event["path"])
    assert len(compiler.tokenizer.encode(result_event["text"]).ids) == len(results)


def test_eos_from_last_completed_call_anchor_stops_stream_delivery(compiler, sample):
    sample["replay"]["1"] = [
        call("time_now", {}, "stream", delivery="stream"),
        call("calculator", {"expression": "1+1"}, "other", **{"result-anchor": 8}),
    ]
    sample["replay"]["{N_TICK:8}+1"] = [eos("stream")]
    plan = compiler.compile(sample)
    assert (
        next(window for window in plan["windows"] if window["id"] == "stream")["close"]
        == 3
    )
    assert max(row["tick"] for row in plan["rows"] if row["window"] == "stream") == 3


def test_same_tick_later_result_can_trigger_eos_cleanup(compiler, sample):
    sample["replay"]["1"] = [
        call("timer", {"mode": "timeout", "seconds": 100}, "cancel"),
        call("calculator", {"expression": "1+1"}, "other", **{"result-anchor": 8}),
    ]
    sample["replay"]["{N_TICK:8}"] = [eos("cancel")]
    plan = compiler.compile(sample)
    cancelled = plan["tool_calls"][0]
    assert cancelled["cancelled"] and cancelled["end"] == 2
    assert "timers" not in compiler.frame(plan, 2)["sandbox"]
    assert not cancelled["outputs"]


def test_trimmed_result_anchor_reschedules_dependents(compiler, sample):
    sample["replay"]["1"] = [
        call("time_now", {}, "stream", delivery="stream", **{"result-anchor": 7}),
        call("calculator", {"expression": "1+1"}, "other", **{"result-anchor": 8}),
    ]
    sample["replay"]["{N_TICK:8}+1"] = [eos("stream")]
    sample["replay"]["{N_TICK:7}+1"] = [
        {"op": "generate", "window": "a", "content": "z"}
    ]
    plan = compiler.compile(sample)
    assert plan["anchors"]["7"]["tick"] == 3
    assert (
        next(
            event
            for event in plan["events"]
            if event["path"] == "replay[{N_TICK:7}+1][0]"
        )["start"]
        == 4
    )


def test_trimmed_anchor_dispatches_followup_at_new_tick(compiler, sample):
    sample["replay"]["1"] = [
        call("time_now", {}, "stream", delivery="stream", **{"result-anchor": 7}),
        call("calculator", {"expression": "1+1"}, "other", **{"result-anchor": 8}),
    ]
    sample["replay"]["{N_TICK:8}+1"] = [eos("stream")]
    sample["replay"]["{N_TICK:7}+1"] = [call("time_now", {}, "followup")]
    plan = compiler.compile(sample)
    followup = plan["tool_calls"][-1]
    assert followup["request_start"] == 4 and followup["start"] == 5
    assert (
        followup["outcome"]["result"]["timestamp"] == "2030-01-01T09:00:00.500000+00:00"
    )


@pytest.mark.parametrize("close_tick", [2, 4])
def test_custom_tool_receives_one_terminal_cleanup_tick(
    tmp_path, compiler, sample, close_tick
):
    (
        tmp_path / "wait.py"
    ).write_text("""from trajectory_remix.tools.runtime import execution_context
name="wait"
schema={"type":"object"}
def run(args,state,windows):
 context=execution_context.get()
 if context.cancelled:
  state["cleaned_at"]=context.tick
  state.pop("waiting",None)
  return "must not be shown",True,True
 state["waiting"]=context.tick
 state.setdefault("ordinary_ticks",[]).append(context.tick)
 return "",False,True
""")
    compiler = Compiler(
        compiler.tokenizer, slot_policy="declaration-v1", tools=ToolRegistry(tmp_path)
    )
    sample["replay"]["1"] = [
        call("wait", {}),
        call("calculator", {"expression": "1+1"}, "other", **{"result-anchor": 8}),
    ]
    sample["replay"][f"{{N_TICK:8}}+{close_tick - 2}"] = [eos("tool")]
    plan = compiler.compile(sample)
    state = compiler.frame(plan, close_tick)["sandbox"]
    assert state == {
        "tick_rate": 10,
        "cleaned_at": close_tick,
        "ordinary_ticks": list(range(2, close_tick + 1)),
    }
    assert plan["tool_calls"][0]["outputs"] == []
