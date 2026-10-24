import copy
import json
from datetime import datetime

import pytest

from trajectory_remix.dataset import trajectory_uuid
from trajectory_remix.llm import SampleBuilder, default_config
from trajectory_remix.tool_sessions import ToolSessions
from trajectory_remix.tools import SCHEMAS, Sandbox, ToolError


NULL_TIME = dict.fromkeys(("year", "month", "day", "hour", "minute", "second", "timezone"))
LEAP_TIME = dict(year=2024, month=2, day=29, hour=12, minute=34, second=56, timezone="UTC")


@pytest.mark.parametrize("zone,offset", [
    ("UTC", "+00:00"), ("Asia/Kolkata", "+05:30"),
    ("America/New_York", "-05:00"), ("+05:45", "+05:45"),
    ("UTC-03:30", "-03:30"),
])
async def test_set_exact_time_and_subsequent_clock_calls(zone, offset):
    sandbox = Sandbox(seconds_per_call=2)
    result = await sandbox.call("time_set", {**LEAP_TIME, "timezone": zone})
    assert result == {"timestamp": f"2024-02-29T12:34:56{offset}", "synthetic": True}
    assert (await sandbox.call("time_now", {}))["timestamp"] == f"2024-02-29T12:34:56.100000{offset}"
    assert (await sandbox.call("time_advance", {"seconds": 60}, invisible=True))["timestamp"] == f"2024-02-29T12:35:56.200000{offset}"
    assert sandbox.calls == 2


def test_nullable_schema_requires_all_seven_fields():
    params = next(s["function"]["parameters"] for s in SCHEMAS if s["function"]["name"] == "time_set")
    assert set(params["required"]) == set(NULL_TIME)
    assert not params["additionalProperties"]
    assert all("null" in spec["type"] for spec in params["properties"].values())


@pytest.mark.parametrize("changes", [
    {"year": True}, {"year": 2024.0}, {"year": "2024"}, {"year": 0}, {"year": 10000},
    {"month": 0}, {"month": 13}, {"day": 0}, {"day": 32}, {"hour": 24},
    {"minute": -1}, {"minute": 60}, {"second": 60}, {"second": float("nan")},
    {"timezone": 5}, {"timezone": "x" * 129},
])
async def test_reject_bad_field_types_and_ranges_before_dispatch(changes):
    sandbox = Sandbox()
    before = sandbox.snapshot()
    with pytest.raises(ToolError):
        await sandbox.call("time_set", {**LEAP_TIME, **changes})
    assert sandbox.snapshot() == before


@pytest.mark.parametrize("args", [
    {k: v for k, v in LEAP_TIME.items() if k != "second"},
    {**LEAP_TIME, "extra": None},
])
async def test_reject_missing_and_extra_fields(args):
    with pytest.raises(ToolError, match="required property|Additional properties"):
        await Sandbox().call("time_set", args)


@pytest.mark.parametrize("changes", [
    {"year": 2023}, {"month": 4, "day": 31}, {"timezone": "No/Such_Zone"},
    {"timezone": ""}, {"timezone": "/etc/passwd"}, {"timezone": "../UTC"},
    {"timezone": "+24:00"}, {"timezone": "+01:60"},
    dict(year=2024, month=3, day=10, hour=2, minute=30, timezone="America/New_York"),
])
async def test_invalid_calendar_and_timezone_leave_only_normal_call_effects(changes):
    sandbox = Sandbox(seed="2030-01-01T00:00:00Z", seconds_per_call=2)
    with pytest.raises(ToolError):
        await sandbox.call("time_set", {**LEAP_TIME, **changes})
    assert sandbox.clock.isoformat() == "2030-01-01T00:00:00+00:00"
    assert sandbox.calls == 1


@pytest.mark.parametrize("fields", [
    NULL_TIME,
    {**LEAP_TIME, "year": None},
    {**LEAP_TIME, "year": None, "month": None, "day": 31},
    {**LEAP_TIME, "month": None, "day": 31},
    {**LEAP_TIME, "day": None},
    dict(year=2024, month=3, day=10, hour=None, minute=30, second=0, timezone="America/New_York"),
    dict(year=2024, month=3, day=10, hour=2, minute=30, second=0, timezone=None),
])
async def test_null_fields_are_reproducible_valid_and_preserve_supplied_fields(fields):
    results = []
    for seed in range(32):
        a, b = Sandbox(random_seed=seed), Sandbox(random_seed=seed)
        first = await a.call("time_set", fields)
        assert await b.call("time_set", fields) == first
        value = datetime.fromisoformat(first["timestamp"])
        assert value.tzinfo is not None
        for key in ("year", "month", "day", "hour", "minute", "second"):
            if fields[key] is not None:
                assert getattr(value, key) == fields[key]
        if fields["year"] is None:
            assert 2000 <= value.year <= 2099
        results.append(first["timestamp"])
    assert len(set(results)) > 1


async def test_random_stream_survives_restore():
    sandbox = Sandbox(random_seed="trajectory-a")
    first = await sandbox.call("time_set", NULL_TIME)
    restored = Sandbox()
    restored.restore(sandbox.snapshot())
    second = await sandbox.call("time_set", NULL_TIME)
    assert await restored.call("time_set", NULL_TIME) == second
    assert second != first


async def test_mcp_discovers_nullable_fields_and_schedules_seeded_time(studio):
    async def command(name, **arguments):
        response = await studio[1].post("/api/mcp/call", json={"name": name, "arguments": arguments})
        assert response.status_code == 200, response.text
        return response.json()

    tools = await command("list_simulated_tools")
    assert any(tool["function"]["name"] == "time_set" for tool in tools["tools"])
    result = await command("create_tool_call", sample_id="test", tick="1", tool="time_set", arguments=NULL_TIME)
    call = result["tool_call"]
    assert call["outcome"]["ok"]
    state = await command("get_sandbox_state", sample_id="test", tick=call["end"])
    assert state["sandbox"]["clock"] == call["outcome"]["result"]["timestamp"]
    await command("undo_edits")
    state = await command("get_sandbox_state", sample_id="test", tick=0)
    assert state["sandbox"].get("calls", 0) == 0


async def test_named_timezone_survives_restore_and_dst_transition():
    sandbox = Sandbox(seconds_per_call=0)
    await sandbox.call("time_set", dict(year=2024, month=3, day=10, hour=1, minute=59, second=59, timezone="America/New_York"))
    restored = Sandbox()
    restored.restore(sandbox.snapshot(), tick=10)
    assert (await restored.call("time_now", {}))["timestamp"] == "2024-03-10T03:00:00-04:00"
    assert await sandbox.call("time_advance", {"seconds": 1}, invisible=True) == {
        "timestamp": "2024-03-10T03:00:00.100000-04:00", "synthetic": True,
    }


def test_replay_uses_trajectory_identity_and_preserves_history(compiler, sample):
    sample["metadata"] = {"synthetic_clock": {"seconds_per_call": 0}}
    sample["replay"]["1"] = [{
        "op": "tool-call", "window": "set-clock", "tool": "time_set", "arguments": NULL_TIME,
        "duration-ticks": 5,
    }]
    plan = compiler.compile(sample)
    completed = plan["tool_calls"][0]["end"]
    expected = Sandbox(random_seed=trajectory_uuid(sample), seconds_per_call=0).call_sync("time_set", NULL_TIME)
    assert plan["tool_calls"][0]["outcome"] == {"ok": True, "result": expected}
    for tick in (completed, 0, completed - 1, completed):
        state = compiler.frame(plan, tick)["sandbox"]
        assert state == {"tick_rate": 10} if tick < completed else state["clock"] == expected["timestamp"]
    assert compiler.compile(sample) == plan
    renamed = {**sample, "uuid": trajectory_uuid(sample), "id": "new label"}
    assert compiler.compile(renamed)["tool_calls"] == plan["tool_calls"]
    other = {**sample, "id": "other trajectory"}
    assert compiler.compile(other)["tool_calls"][0]["outcome"] != plan["tool_calls"][0]["outcome"]


def test_external_time_set_has_no_tokens_or_call_cost(compiler, sample):
    sample["replay"]["1"] = [{"op": "invisible-tool-call", "tool": "time_set", "arguments": LEAP_TIME}]
    plan = compiler.compile(sample)
    state = compiler.frame(plan, 1)["sandbox"]
    assert state["clock"] == "2024-02-29T12:34:56+00:00"
    assert state["calls"] == 0
    assert not any(w["type"] == "tool" for w in plan["windows"])
    assert not any("time_set" in e.get("text", "") for e in plan["events"])


async def test_workbench_reset_and_session_isolation():
    sessions = ToolSessions()
    sessions.create("a", {"seed": "2031-01-01T00:00:00Z"})
    sessions.create("b", {"seed": "2031-01-01T00:00:00Z"})
    first = await sessions.call("a", "time_set", NULL_TIME)
    assert sessions.inspect("b")["state"] == {"tick_rate": 10}
    sessions.reset("a")
    assert (await sessions.call("a", "time_set", NULL_TIME))["interaction"]["outcome"] == first["interaction"]["outcome"]


@pytest.mark.parametrize("continue_existing", [False, True])
async def test_generation_and_replay_agree_on_randomized_time(compiler, sample, continue_existing):
    config = default_config()
    config["tasks"] = [config["tasks"][2]]
    config["tasks"][0]["tools"] = True
    if continue_existing:
        sample["replay"]["1"] = [{"op": "tool-call", "window": "clock", "tool": "time_set", "arguments": NULL_TIME}]

    class Client:
        def __init__(self):
            self.outcome = None

        async def complete(self, endpoint, messages, tools=None):
            if messages[-1]["role"] == "tool":
                self.outcome = json.loads(messages[-1]["content"])
                return {"content": "Time set."}
            return {"content": None, "tool_calls": [{
                "id": "set-time", "type": "function",
                "function": {"name": "time_set", "arguments": json.dumps(NULL_TIME)},
            }]}

    client = Client()
    result = await SampleBuilder(compiler, config, client).generate(seed=copy.deepcopy(sample) if continue_existing else None)
    plan = compiler.compile(result)
    assert client.outcome["ok"]
    assert plan["tool_calls"][-1]["outcome"] == client.outcome
    assert compiler.frame(plan, plan["max_tick"])["sandbox"]["clock"] == client.outcome["result"]["timestamp"]
