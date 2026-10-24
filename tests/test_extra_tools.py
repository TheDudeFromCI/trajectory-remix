import copy
import json

import pytest

from trajectory_remix.compiler import CompileError
from trajectory_remix.dataset import trajectory_uuid
from trajectory_remix.tools import Sandbox, ToolError


def call(name, args, window="tool"):
    return {
        "op": "tool-call",
        "tool": name,
        "arguments": args,
        "window": window,
        "content": "x",
    }


def invisible(name, args):
    return {"op": "invisible-tool-call", "tool": name, "arguments": args}


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 3 * 4", 14),
        ("sin(pi / 2)", 1),
        ("acos(0)", 1.5707963267948966),
        ("sqrt(81) + log(8, 2)", 12),
        ("round(exp(1), 3)", 2.718),
        ("max(2, abs(-5)) + floor(1.9)", 6),
        ("2 ** 10", 1024),
    ],
)
def test_calculator_library_functions(expression, expected):
    sandbox = Sandbox()
    assert sandbox.call_sync("calculator", {"expression": expression})[
        "value"
    ] == pytest.approx(expected)
    assert sandbox.snapshot() == {}


@pytest.mark.parametrize(
    "expression",
    [
        "1/0",
        "sqrt(-1)",
        "__import__('os')",
        "open('file')",
        "pi.__class__",
        "'abc' * 100",
        "[x for x in (1,2)]",
        "9 ** 999999999",
        "1e309",
    ],
)
def test_calculator_rejects_non_math_and_bad_results(expression):
    with pytest.raises(ToolError):
        Sandbox().call_sync("calculator", {"expression": expression})


def test_random_types_ranges_determinism_and_resume(compiler, sample):
    requests = [
        {"kind": "int", "min": -5, "max": -5},
        {"kind": "bool"},
        {"kind": "float", "min": 2, "max": 3},
        {"kind": "choice", "choices": [{"nested": [1, 2]}]},
    ]
    for index, args in enumerate(requests):
        sample["replay"][str(index * 3 + 1)] = [call("random", args, f"r{index}")]
    plan = compiler.compile(sample)
    assert compiler.compile(sample) == plan
    values = [item["outcome"]["result"]["value"] for item in plan["tool_calls"]]
    assert values[0] == -5 and type(values[1]) is bool and 2 <= values[2] <= 3
    assert values[3] == {"nested": [1, 2]}
    assert compiler.frame(plan, 0)["sandbox"] == {}
    assert compiler.frame(plan, plan["max_tick"])["sandbox"]["random"]["counter"] == 4
    sandbox = Sandbox(random_seed=trajectory_uuid(sample))
    restored = Sandbox(random_seed=trajectory_uuid(sample))
    sandbox.call_sync("random", {"kind": "float"})
    restored.restore(sandbox.snapshot())
    assert restored.call_sync("random", {"kind": "float"}) == sandbox.call_sync(
        "random", {"kind": "float"}
    )
    before = copy.deepcopy(sandbox.snapshot())
    with pytest.raises(ToolError, match="min must"):
        sandbox.call_sync("random", {"kind": "int", "min": 5, "max": 1})
    assert sandbox.snapshot() == before


@pytest.mark.parametrize(
    "args",
    [
        {"kind": "choice", "choices": []},
        {"kind": "choice"},
        {"kind": "int", "min": 0.5},
        {"kind": "unknown"},
    ],
)
def test_random_rejects_invalid_requests(args):
    with pytest.raises(ToolError):
        Sandbox().call_sync("random", args)


def test_tick_timers_and_interval_outputs_do_not_create_state(compiler, sample):
    sample["replay"]["1"] = [
        call("timer", {"mode": "timeout", "ticks": 3}, "timeout"),
        call("timer", {"mode": "interval", "ticks": 2, "count": 3}, "interval"),
        call("timer", {"mode": "alarm", "at_tick": 5}, "alarm"),
    ]
    plan = compiler.compile(sample)
    timeout, interval, alarm = plan["tool_calls"]
    assert (timeout["start"], timeout["end"]) == (2, 5)
    assert [update["tick"] for update in interval["outputs"]] == [4, 6, 8]
    assert interval["end"] == 8 and alarm["end"] == 5
    assert plan["sandbox"]["snapshots"] == {}
    assert "firing" not in compiler.frame(plan, 3)["windows"]["interval"]["text"]


def test_timestamp_alarm_uses_authored_clock_not_host_time(compiler, sample):
    sample["metadata"] = {
        "synthetic_clock": {"seed": "2030-01-01T00:00:00Z", "seconds_per_call": 0}
    }
    sample["replay"]["1"] = [
        call("timer", {"mode": "alarm", "timestamp": "2030-01-01T00:00:10+00:00"})
    ]
    sample["replay"]["5"] = [invisible("time_advance", {"seconds": 10})]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["end"] == 6
    assert compiler.frame(plan, 4)["sandbox"] == {}
    sample["replay"]["1"][0]["arguments"]["timeout_ticks"] = 1
    assert compiler.compile(sample)["tool_calls"][0]["outcome"]["ok"] is False


@pytest.mark.parametrize(
    "args",
    [
        {"mode": "interval", "ticks": 0, "count": 2},
        {"mode": "interval", "ticks": 1},
        {"mode": "alarm"},
        {"mode": "alarm", "at_tick": 2, "timestamp": "2030-01-01T00:00:00Z"},
    ],
)
def test_timer_requires_bounded_valid_schedules(args):
    with pytest.raises(ToolError):
        Sandbox().call_sync("timer", args)


def test_diagnostics_are_author_configured_and_time_scoped(compiler, sample):
    sample["replay"].update(
        {
            "1": [call("system_diagnostics", {}, "before")],
            "5": [
                invisible(
                    "system_diagnostics_set",
                    {"cpu_usage_percent": 83.25, "cpu_cores": 32, "os": "ExampleOS"},
                )
            ],
            "7": [call("system_diagnostics", {}, "after")],
        }
    )
    plan = compiler.compile(sample)
    before, _, after = plan["tool_calls"]
    assert before["outcome"]["result"]["cpu_usage_percent"] == 0
    assert before["outcome"]["result"]["synthetic"] is True
    assert after["outcome"]["result"]["cpu_cores"] == 32
    assert after["outcome"]["result"]["os"] == "ExampleOS"
    assert compiler.frame(plan, 4)["sandbox"] == {}
    sandbox = Sandbox()
    sandbox.call_sync(
        "system_diagnostics_set",
        {"ram_total_bytes": 10, "ram_used_bytes": 5},
        invisible=True,
    )
    original = sandbox.snapshot()
    with pytest.raises(ToolError, match="cannot exceed"):
        sandbox.call_sync(
            "system_diagnostics_set", {"ram_used_bytes": 11}, invisible=True
        )
    assert sandbox.snapshot() == original


def test_configuration_tools_remain_invisible(compiler, sample):
    for name in ("system_diagnostics_set", "hardware_load_set"):
        assert name not in {s["function"]["name"] for s in compiler.tools.schemas()}
        sample["replay"]["1"] = [
            call(
                name,
                {"file_io_ticks": 3}
                if name == "hardware_load_set"
                else {"cpu_cores": 2},
            )
        ]
        with pytest.raises(CompileError, match="invisible-only"):
            compiler.compile(sample)


def test_hardware_load_delays_file_commit_and_freezes_in_flight_latency(
    compiler, sample
):
    sample["replay"]["0"] += [
        invisible("file_write", {"path": "x", "content": "old"}),
        invisible(
            "hardware_load_set",
            {"file_io_ticks": 3, "operation_ticks": {"file_read": 1}},
        ),
    ]
    sample["replay"].update(
        {
            "1": [call("file_write", {"path": "x", "content": "new"}, "write")],
            "3": [call("file_read", {"path": "x"}, "read")],
            "4": [invisible("hardware_load_set", {"file_io_ticks": 8})],
            "7": [call("file_write", {"path": "y", "content": "later"}, "later")],
        }
    )
    plan = compiler.compile(sample)
    write = next(c for c in plan["tool_calls"] if c.get("window") == "write")
    read = next(c for c in plan["tool_calls"] if c.get("window") == "read")
    later = next(c for c in plan["tool_calls"] if c.get("window") == "later")
    assert (write["start"], write["end"]) == (2, 5)
    assert (read["start"], read["end"]) == (4, 5)
    assert read["outcome"]["result"]["content"] == "new"
    assert (later["start"], later["end"]) == (8, 16)
    assert compiler.frame(plan, 4)["sandbox"]["files"]["/x"] == "old"
    assert compiler.frame(plan, 5)["sandbox"]["files"]["/x"] == "new"
    assert "file_io_pending" not in compiler.frame(plan, plan["max_tick"])["sandbox"]
    assert compiler.compile(sample) == plan


def test_file_load_adds_to_legacy_duration_and_zero_does_not_resample(compiler, sample):
    sample["replay"]["0"].append(invisible("hardware_load_set", {"file_io_ticks": 2}))
    sample["replay"]["1"] = [{**call("file_list", {}), "duration-ticks": 3}]
    assert compiler.compile(sample)["tool_calls"][1]["end"] == 7
    sample["replay"]["0"].pop()
    sample["replay"]["3"] = [invisible("hardware_load_set", {"file_io_ticks": 100})]
    assert compiler.compile(sample)["tool_calls"][0]["end"] == 5


def test_file_watch_emits_completed_create_modify_delete_events(compiler, sample):
    sample["replay"].update(
        {
            "1": [call("file_watch", {"path": "x", "count": 3})],
            "3": [invisible("file_write", {"path": "x", "content": "first"})],
            "5": [invisible("file_write", {"path": "x", "content": "second"})],
            "7": [invisible("file_delete", {"path": "x"})],
        }
    )
    plan = compiler.compile(sample)
    watcher = plan["tool_calls"][0]
    observations = [
        json.loads(output["output"])["result"] for output in watcher["outputs"]
    ]
    assert [(event["tick"], event["event"]) for event in observations] == [
        (4, "created"),
        (6, "modified"),
        (8, "deleted"),
    ]
    assert watcher["end"] == 8
    assert "file_watchers" not in compiler.frame(plan, 8)["sandbox"]
    assert "modified" not in compiler.frame(plan, 4)["windows"]["tool"]["text"]
    sample["replay"]["1"][0]["arguments"].update(events=["modified"], count=1)
    filtered = compiler.compile(sample)["tool_calls"][0]
    assert filtered["end"] == 6 and len(filtered["outputs"]) == 1


def test_file_watch_timeout_is_a_failed_observation_and_cleans_up(compiler, sample):
    sample["replay"]["1"] = [
        call("file_watch", {"path": "missing", "timeout_ticks": 2})
    ]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["end"] == 4
    assert not plan["tool_calls"][0]["outcome"]["ok"]
    assert compiler.frame(plan, 4)["sandbox"] == {}
