import json
import re

from trajectory_remix.tools import DEFINITIONS


async def command(studio, name, **arguments):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": name, "arguments": arguments}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_guide_is_available_without_repository_or_provider_access(studio):
    guide = (await command(studio, "get_authoring_guide"))["content"]
    definitions = (await command(studio, "list_simulated_tools"))["tools"]
    assert f"All {len(DEFINITIONS)} tools" in guide
    assert all(f"`{name}`" in guide for name, _, _ in DEFINITIONS)
    assert all(t["available"] for t in definitions)
    assert next(t for t in definitions if t["function"]["name"] == "subagent")[
        "requires_authored_outcome"
    ]
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
    guide = (await command(studio, "get_authoring_guide"))["content"]
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
    for arguments in worked_calls:
        await command(studio, "create_tool_call", **arguments)
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
        duration_ticks=12,
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
        tick="12",
        tool="runtime_status",
        window="status",
        arguments={},
    )
    await command(
        studio,
        "create_tool_call",
        sample_id=sid,
        tick="13",
        tool="chat",
        window="status",
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
    before = compiler.frame(plan, 17)["sandbox"]
    assert (
        not before["files"] and len(before["conversations"]["coder"]["messages"]) == 1
    )
    assert compiler.frame(plan, 18)["sandbox"]["files"]["/math.py"].startswith(
        "def double"
    )
    during = compiler.frame(plan, 20)["sandbox"]
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
