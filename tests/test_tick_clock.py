from datetime import UTC, datetime
import copy

import pytest

from trajectory_remix.compiler import CompileError
from trajectory_remix.tools import Sandbox


def call(tool, arguments, window="clock"):
    return {
        "op": "tool-call",
        "tool": tool,
        "arguments": arguments,
        "window": window,
        "content": "",
    }


def hidden(tool, arguments):
    return {"op": "invisible-tool-call", "tool": tool, "arguments": arguments}


def fields(unix):
    value = datetime.fromtimestamp(unix, UTC)
    return {
        **{
            key: getattr(value, key)
            for key in ("year", "month", "day", "hour", "minute", "second")
        },
        "timezone": "UTC",
    }


def test_unix_tick_anchor_matches_the_requested_example(compiler, sample):
    base = 234802938402
    sample["replay"].update(
        {
            "14": [hidden("time_set", fields(base))],
            "24": [call("time_now", {}, "read1")],
            "34": [hidden("time_advance", {"seconds": 2})],
            "44": [call("time_now", {}, "read2")],
        }
    )
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 0)["sandbox"] == {"tick_rate": 10}
    anchor = compiler.frame(plan, 14)["sandbox"]
    assert (anchor["clock_unix"], anchor["clock_tick"], anchor["tick_rate"]) == (
        base,
        14,
        10,
    )
    assert (
        datetime.fromisoformat(
            plan["tool_calls"][1]["outcome"]["result"]["timestamp"]
        ).timestamp()
        == base + 1
    )
    assert (
        compiler.frame(plan, 24)["sandbox"]["clock_tick"] == 14
    )  # Reading doesn't rebase.
    updated = compiler.frame(plan, 34)["sandbox"]
    assert updated["clock_unix"] == base + 4 and updated["clock_tick"] == 34
    assert (
        datetime.fromisoformat(
            plan["tool_calls"][3]["outcome"]["result"]["timestamp"]
        ).timestamp()
        == base + 5
    )
    assert set(plan["sandbox"]["snapshots"]) == {"14", "24", "34", "44"}
    assert compiler.compile(sample) == plan


@pytest.mark.parametrize("rate", [2.5, 10, 20])
def test_rate_is_trajectory_specific_and_shared_with_tools(compiler, sample, rate):
    sample["metadata"] = {
        "synthetic_clock": {"tick_rate": rate, "seed": "2030-01-01T00:00:00Z"}
    }
    sample["replay"]["10"] = [call("time_now", {})]
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 0)["sandbox"]["tick_rate"] == rate
    expected = datetime(2030, 1, 1, tzinfo=UTC).timestamp() + 10 / rate
    assert (
        datetime.fromisoformat(
            plan["tool_calls"][0]["outcome"]["result"]["timestamp"]
        ).timestamp()
        == expected
    )
    assert compiler.frame(plan, plan["max_tick"])["sandbox"]["clock_tick"] == 0


@pytest.mark.parametrize("rate", [0, -1, True, "10", float("inf"), float("nan")])
def test_invalid_rates_are_compile_diagnostics(compiler, sample, rate):
    sample["metadata"] = {"synthetic_clock": {"tick_rate": rate}}
    with pytest.raises(CompileError, match="tick_rate must"):
        compiler.compile(sample)


def test_calls_do_not_advance_time_within_the_same_tick(compiler, sample):
    sample["replay"]["10"] = [call("time_now", {}, "one"), call("time_now", {}, "two")]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["outcome"] == plan["tool_calls"][1]["outcome"]


@pytest.mark.parametrize("rate,end", [(10, 12), (20, 22), (2.5, 5)])
def test_second_timeout_scales_with_tick_rate(compiler, sample, rate, end):
    sample["metadata"] = {"synthetic_clock": {"tick_rate": rate}}
    sample["replay"]["1"] = [
        {**call("timer", {"mode": "timeout", "seconds": 1}), "content": "x"}
    ]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["start"] == 2
    assert plan["tool_calls"][0]["end"] == end
    assert "timers" not in compiler.frame(plan, plan["max_tick"])["sandbox"]
    assert len(plan["sandbox"]["snapshots"]) == 2  # Register and complete only.


def test_second_timers_and_watch_timeouts_follow_clock_advancement(compiler, sample):
    sample["replay"].update(
        {
            "1": [
                call("timer", {"mode": "timeout", "seconds": 30}, "timer"),
                call("file_watch", {"path": "missing", "timeout_seconds": 30}, "watch"),
            ],
            "5": [hidden("time_advance", {"seconds": 60})],
        }
    )
    plan = compiler.compile(sample)
    assert [item["end"] for item in plan["tool_calls"][:2]] == [6, 6]
    assert plan["tool_calls"][0]["outcome"]["ok"]
    assert not plan["tool_calls"][1]["outcome"]["ok"]
    final = compiler.frame(plan, plan["max_tick"])["sandbox"]
    assert "timers" not in final and "file_watchers" not in final


def test_interval_seconds_and_timestamp_alarm_advance_with_ticks(compiler, sample):
    sample["metadata"] = {
        "synthetic_clock": {"seed": "2030-01-01T00:00:00Z", "tick_rate": 10}
    }
    sample["replay"]["1"] = [
        call("timer", {"mode": "interval", "seconds": 0.5, "count": 3}, "interval"),
        call("timer", {"mode": "alarm", "timestamp": "2030-01-01T00:00:01Z"}, "alarm"),
    ]
    plan = compiler.compile(sample)
    assert [item["tick"] for item in plan["tool_calls"][0]["outputs"]] == [6, 11, 16]
    assert plan["tool_calls"][1]["end"] == 10


def test_workbench_resume_can_supply_current_tick():
    first = Sandbox(tick_rate=20)
    first.call_sync("time_set", fields(1893456000))
    state = copy.deepcopy(first.snapshot())
    resumed = Sandbox(tick_rate=20)
    resumed.restore(state, tick=40)
    assert resumed.call_sync("time_now", {})["timestamp"] == "2030-01-01T00:00:02+00:00"


async def test_metadata_rate_edit_is_persisted_and_applied(studio):
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "update_trajectory_metadata",
            "arguments": {
                "sample_id": "test",
                "patch": {"metadata": {"synthetic_clock": {"tick_rate": 25}}},
            },
        },
    )
    assert response.status_code == 200, response.text
    plan = studio[0].state.compiler.compile(studio[0].state.dataset["samples"][0])
    assert plan["sandbox"]["initial"]["tick_rate"] == 25


def test_unix_anchor_alone_can_be_read_by_time_tools(compiler):
    from trajectory_remix.tools import ExecutionContext
    import json

    state = {"tick_rate": 10, "clock_unix": 234802938402, "clock_tick": 14}
    output, done, modified = compiler.tools.run_tick(
        "time_now", {}, state, [], ExecutionContext(tick=24)
    )
    assert (
        datetime.fromisoformat(json.loads(output)["result"]["timestamp"]).timestamp()
        == 234802938403
    )
    assert done and modified


def test_fractional_timeout_avoids_large_unix_float_cancellation(compiler, sample):
    sample["replay"].update(
        {
            "14": [hidden("time_set", fields(234802938402))],
            "20": [call("timer", {"mode": "timeout", "seconds": 0.3})],
        }
    )
    assert compiler.compile(sample)["tool_calls"][1]["end"] == 23
