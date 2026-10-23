import copy
import json
from pathlib import Path

import pytest

from trajectory_remix.coordination import audit
from trajectory_remix.execution import DockerExecutor


@pytest.fixture
def benchmark():
    return json.loads(
        (Path(__file__).parents[1] / "examples/coordination-benchmark.json").read_text()
    )


def test_live_benchmark_replays_offline_and_audit_rejects_unsupported_claims(
    benchmark, compiler, monkeypatch
):
    async def forbidden(*args, **kwargs):
        pytest.fail("Offline benchmark validation must never execute code")

    monkeypatch.setattr(DockerExecutor, "run", forbidden)
    plan = compiler.compile(benchmark)
    state = compiler.frame(plan, plan["max_tick"])["sandbox"]
    report = audit(benchmark, plan, state)
    assert not plan["diagnostics"] and report["valid"]
    assert {
        k: report[k]
        for k in (
            "subagents",
            "workers",
            "conversations_with_followups",
            "jobs",
            "completed_tasks",
            "passing_executions",
            "coordinator_file_writes",
        )
    } == {
        "subagents": 6,
        "workers": 6,
        "conversations_with_followups": 6,
        "jobs": 14,
        "completed_tasks": 6,
        "passing_executions": 22,
        "coordinator_file_writes": 0,
    }
    assert len(plan["rows"]) > 50_000 and all(
        w["close"] is not None for w in plan["windows"]
    )
    # A file edit invalidates both task evidence and final reviewer context.
    stale = copy.deepcopy(state)
    stale["files"]["/slugify.py"] += "\n# changed after review\n"
    rejected = audit(benchmark, plan, stale)
    assert not rejected["valid"]
    assert any("stale tested files" in d["message"] for d in rejected["diagnostics"])
    assert any("stale source" in d["message"] for d in rejected["diagnostics"])
    # Review context cannot be replaced by tests/docs alone.
    blind = copy.deepcopy(state)
    for job in blind["jobs"].values():
        if job["arguments"].get("role") == "review":
            job["arguments"]["artifacts"] = []
    assert any(
        "implementation source" in d["message"]
        for d in audit(benchmark, plan, blind)["diagnostics"]
    )
    coordinator = {**plan, "tool_calls": [*plan["tool_calls"], {"tool": "file_write"}]}
    assert not audit(benchmark, coordinator, state)["valid"]


async def test_large_benchmark_supports_compact_dashboard_and_mcp_snapshots(
    studio, benchmark
):
    response = await studio[1].post("/api/compile", json={"sample": benchmark})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["rows_omitted"] and plan["row_count"] > 50_000
    assert plan["rows"] == [] and plan["sandbox"] is None
    frame = await studio[1].get(f"/api/frame/{plan['plan_id']}/{plan['max_tick']}")
    assert frame.status_code == 200 and len(frame.json()["windows"]) == 2
    studio[0].state.dataset["samples"] = [benchmark]
    snapshots = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "preview_snapshots",
            "arguments": {
                "sample_id": benchmark["id"],
                "start_tick": 0,
                "end_tick": plan["max_tick"],
                "step": plan["max_tick"],
            },
        },
    )
    assert snapshots.status_code == 200, snapshots.text
    assert len(snapshots.json()["frames"]) == 2
