import json
import re

import pytest

from trajectory_remix.tools import DEFINITIONS


async def command(studio, name, **arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": name, "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def lesson(studio, topic):
    """Run the published MCP examples, supplying the session values they request."""
    guide = (await command(studio, "get_authoring_guide", topic=topic))["content"]
    results = []
    for block in re.findall(r"```json\n(.*?)\n```", guide, re.S):
        session = await command(studio, "get_session")
        example = json.loads(block.replace("$EOS", session["special_tokens"]["eos"]))
        if example["name"] not in {"list_events", "get_sandbox_state"}:
            example["arguments"]["expected_revision"] = session["revision"]
        results.append(await command(studio, example["name"], **example["arguments"]))
    return results


async def test_default_guide_is_short_and_topics_are_explicit(studio):
    start = (await command(studio, "get_authoring_guide"))["content"]
    assert "Start here: one lesson at a time" in start
    assert len(start.split()) < 600
    for topic in ("first", "parallel", "edit", "sandbox", "tools"):
        guide = (await command(studio, "get_authoring_guide", topic=topic))["content"]
        assert guide.startswith("# ") and len(guide.split()) < 1200
        assert f"`{topic}`" in start
    rejected = await studio[1].post(
        "/api/mcp/call",
        json={"name": "get_authoring_guide", "arguments": {"topic": "../Skill"}},
    )
    assert rejected.status_code == 422
    assert (await command(studio, "get_session"))["revision"] == 0


async def test_example_topic_returns_complete_json_without_modifying_dataset(studio):
    before = await command(studio, "get_session")
    response = await command(studio, "get_authoring_guide", topic="example")
    sample = json.loads(response["content"])
    assert sample["schema-version"] == "1.0"
    assert sample["id"] == "8cb8c91f-b8a6-48e4-8abb-9cb9cb328264"
    assert [event["tool"] for event in sample["replay"]["2"]] == [
        "file_list", "file_read"
    ]
    assert [e["op"] for e in sample["replay"]["0"]].count("invisible-tool-call") == 7
    after = await command(studio, "get_session")
    assert after["revision"] == before["revision"]
    assert after["sample_count"] == before["sample_count"]


async def test_first_lesson_and_local_repairs_preserve_causality_and_closure(studio):
    await lesson(studio, "first")
    sid = "first-steps"
    assert (await command(studio, "validate_trajectory", sample_id=sid))["valid"]
    before = await command(studio, "get_window_history", sample_id=sid, window="chat")
    anchors = (await command(studio, "get_anchors", sample_id=sid))["anchors"]
    first_token = min(r["tick"] for r in before["rows"] if r["op"] == "generate")
    assert first_token > anchors["1"]["tick"]
    assert "Hello!" in before["text"]

    await lesson(studio, "edit")
    after = await command(studio, "get_window_history", sample_id=sid, window="chat")
    assert "Hello there!" in after["text"]
    assert min(r["tick"] for r in after["rows"] if r["op"] == "generate") == first_token + 2
    plan = studio[0].state.commands._plan(sid)
    assert all(w["close"] is not None for w in plan["windows"])
    user_rows = [r for r in plan["rows"] if r["window"] == "request" and r["op"] == "append"]
    assert user_rows and all(r["loss_weight"] == 0 for r in user_rows)


async def test_parallel_lesson_overlaps_and_resumes_without_future_answers(studio):
    class Forbidden:
        async def complete(self, *args, **kwargs):
            pytest.fail("A lesson must never call a provider")

    studio[0].state.commands.client = Forbidden()
    results = await lesson(studio, "parallel")
    api, writing, followup = [r["tool_call"] for r in results if "tool_call" in r]
    assert max(api["start"], writing["start"]) < min(api["end"], writing["end"])
    assert api["result_anchor"] == 1
    assert followup["request_start"] == api["result_end"] + 1
    assert followup["start"] < writing["end"]
    sid = "parallel-start"
    during = (await command(studio, "get_sandbox_state", sample_id=sid,
                            tick=max(api["start"], writing["start"])))["sandbox"]
    assert len(during["conversations"]) == 2
    assert all(c["status"] == "running" for c in during["conversations"].values())
    assert all([m["role"] for m in c["messages"]] == ["user"]
               for c in during["conversations"].values())
    report = await command(studio, "validate_trajectory", sample_id=sid)
    assert report["valid"]
    final = (await command(studio, "get_sandbox_state", sample_id=sid,
                           tick=report["max_tick"]))["sandbox"]
    assert [m["role"] for m in final["conversations"]["api-specialist"]["messages"]] == [
        "user", "assistant", "user", "assistant"
    ]
    plan = studio[0].state.commands._plan(sid)
    result_paths = {e["path"] for e in plan["events"] if e["path"].endswith(".result")}
    assert result_paths
    assert all(r["loss_weight"] == 0 for r in plan["rows"] if r["event"] in result_paths)


async def test_sandbox_lesson_seeds_replay_state_and_reads_it_visibly(studio):
    results = await lesson(studio, "sandbox")
    assert results[2]["sandbox"]["files"]["/math.py"] == "def double(value):\n    return value * 2\n"
    read = results[-1]["tool_call"]
    assert read["outcome"]["ok"] and "return value * 2" in read["outcome"]["result"]["content"]
    plan = studio[0].state.commands._plan("sandbox-start")
    assert plan["tool_calls"][0]["invisible"]
    assert not any(r["event"] == plan["tool_calls"][0]["path"] for r in plan["rows"])
    assert not studio[0].state.commands.tool_sessions.sessions


async def test_guide_is_available_without_repository_or_provider_access(studio):
    guide = (await command(studio, "get_authoring_guide", topic="reference"))["content"]
    definitions = (await command(studio, "list_simulated_tools"))["tools"]
    assert f"The {len(DEFINITIONS)} built-in tools" in guide
    assert all(f"`{name}`" in guide for name, _, _ in DEFINITIONS)
    assert all(t["available"] == (not t.get("invisible", False)) for t in definitions)
    assert not {"python_run", "subagent_wait", "subagent_cancel"} & {
        t["function"]["name"] for t in definitions
    }


async def test_guide_worked_calls_preserve_parallel_timing_context_and_masks(
    studio, compiler
):
    class Forbidden:
        async def complete(self, *args, **kwargs):
            raise AssertionError("The authoring workflow must not call a provider")

    studio[0].state.commands.client = Forbidden()
    guide = (await command(studio, "get_authoring_guide", topic="reference"))["content"]
    worked_calls = [
        json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", guide, re.S)
    ]
    assert len(worked_calls) == 3
    sid = "parallel-example"
    await command(studio, "create_trajectory", sample_id=sid)
    await command(
        studio,
        "create_event",
        sample_id=sid,
        tick="0",
        event={"op": "open", "window": "chat"},
    )
    await command(
        studio,
        "create_event",
        sample_id=sid,
        tick="0",
        event={
            "op": "append",
            "window": "chat",
            "source": "user",
            "content": "User: Implement double and document it.\n",
            "loss": {"mask": "exclude"},
        },
    )
    scheduled = [await command(studio, "create_tool_call", **arguments)
                 for arguments in worked_calls]
    coder, followup, external = [result["tool_call"] for result in scheduled]
    await command(
        studio,
        "create_tool_call",
        sample_id=sid,
        tick="11",
        tool="subagent",
        window="docs-call",
        arguments={
            "task": "Draft numeric double API documentation from the requirement."
        },
        content="x",
        duration_ticks=coder["end"] + 1000,
        outcome={
            "ok": True,
            "result": {
                "conversationId": "docs",
                "response": "Drafted a numeric API description; source review is still needed.",
            },
        },
    )
    await command(
        studio,
        "create_tool_call",
        sample_id=sid,
        tick=str(coder["start"] + 1),
        tool="runtime_status",
        window="status",
        arguments={},
    )
    await command(
        studio,
        "create_tool_call",
        sample_id=sid,
        tick=str(coder["start"] + 2),
        tool="chat",
        window="status-message",
        arguments={"content": "Both specialists are working."},
    )
    sample = (await command(studio, "get_trajectory", sample_id=sid))["sample"]
    plan = compiler.compile(sample)
    status = next(
        c["outcome"]["result"]
        for c in plan["tool_calls"]
        if c["tool"] == "runtime_status"
    )
    assert {c["status"] for c in status["subagents"]} == {"running"}
    assert len(status["subagents"]) == 2
    before = compiler.frame(plan, coder["end"] - 1)["sandbox"]
    assert (
        not before["files"] and len(before["conversations"]["coder"]["messages"]) == 1
    )
    assert compiler.frame(plan, external["end"])["sandbox"]["files"]["/math.py"].startswith(
        "def double"
    )
    during = compiler.frame(plan, followup["start"] + 1)["sandbox"]
    assert during["conversations"]["coder"]["status"] == "running"
    assert during["conversations"]["docs"]["status"] == "running"
    final = compiler.frame(plan, plan["max_tick"])
    assert [
        m["role"] for m in final["sandbox"]["conversations"]["coder"]["messages"]
    ] == ["user", "assistant", "user", "assistant"]
    assert "def double" not in json.dumps(final["windows"])
    assert not any(
        c["tool"] == "file_write" and not c.get("invisible") for c in plan["tool_calls"]
    )
    observations = [e for e in plan["events"] if e["path"].endswith(".result")]
    assert observations
    for event in observations:
        rows = [r for r in plan["rows"] if r["event"] == event["path"]]
        assert rows and all(r["loss_weight"] == 0 for r in rows)
    eos = studio[0].state.commands.compiler.special["eos"]
    await command(
        studio,
        "apply_batch",
        edits=[
            {
                "name": "create_event",
                "arguments": {
                    "sample_id": sid,
                    "tick": str(plan["max_tick"] + 1),
                    "event": {"op": "generate", "window": window["id"], "content": eos},
                },
            }
            for window in plan["windows"]
        ],
    )
    assert (await command(studio, "validate_trajectory", sample_id=sid))["valid"]
