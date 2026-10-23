import copy
import hashlib
import json
from pathlib import Path

import pytest

from trajectory_remix.compiler import CompileError, chunks
from trajectory_remix.tools import Sandbox, ToolError


def external(tool, arguments, **extra):
    return {"op": "invisible-tool-call", "tool": tool, "arguments": arguments, **extra}


def agent(task, cid, response="answer", resume=None, duration=0, **extra):
    return {
        "op": "tool-call",
        "tool": "subagent",
        "window": cid,
        "content": "x",  # Keep lifecycle tests independent of request tokenization.
        "arguments": {"task": task, **({"resume": resume} if resume else {}), **extra},
        "duration-ticks": duration,
        "outcome": {
            "ok": True,
            "result": {"conversationId": cid, "response": response},
        },
    }


def test_invisible_events_never_add_training_rows_windows_or_export_chunks(
    compiler, sample
):
    baseline = compiler.compile(sample)
    sample["replay"]["500"] = [
        external(
            "file_write",
            {"path": "/secret", "content": "external implementation"},
            source="subagent",
        )
    ]
    plan = compiler.compile(sample)
    assert plan["rows"] == baseline["rows"]
    assert plan["windows"] == baseline["windows"]
    assert plan["events"] == baseline["events"]
    assert list(chunks(plan, 8)) == list(chunks(baseline, 8))
    assert plan["max_tick"] == 500 and plan["token_max_tick"] == 0
    assert compiler.frame(plan, 499)["sandbox"]["files"] == {}
    state = compiler.frame(plan, 500)["sandbox"]
    assert state["files"] == {"/secret": "external implementation"}
    assert state["calls"] == 0
    assert state["clock"] == baseline["sandbox"]["initial"]["clock"]
    assert state["tool_calls"][0]["source"] == "subagent"


def test_external_writes_share_completion_order_and_can_use_anchors(compiler, sample):
    sample["replay"].update(
        {
            "1": [{"op": "generate", "window": "a", "content": "x{N_TICK:1}"}],
            "{N_TICK:1}+1": [
                external(
                    "file_write",
                    {"path": "x", "content": "provided"},
                    **{"duration-ticks": 3},
                ),
                {
                    "op": "tool-call",
                    "tool": "file_read",
                    "arguments": {"path": "x"},
                    "window": "read",
                    "duration-ticks": 2,
                    "content": "x",
                },
            ],
        }
    )
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 4)["sandbox"]["files"] == {}
    assert plan["tool_calls"][-1]["outcome"]["result"]["content"] == "provided"
    assert compiler.frame(plan, 5)["sandbox"]["calls"] == 1
    assert not any(
        "provided" in e["text"] for e in plan["events"] if e["window"] != "read"
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"window": "hidden"},
        {"content": "secret"},
        {"loss": {"mask": "include"}},
        {"delivery": "stream"},
        {"tool": "chat"},
        {"tool": "subagent"},
        {"arguments": {"path": "../host", "content": "escape"}},
        {"tool": "file_delete", "arguments": {"path": "missing"}},
    ],
)
def test_invalid_invisible_events_are_diagnosed_for_author(compiler, sample, extra):
    sample["replay"]["1"] = [
        {**external("file_write", {"path": "x", "content": "data"}), **extra}
    ]
    with pytest.raises(CompileError) as error:
        compiler.compile(sample)
    assert error.value.path == "replay[1][0]"


async def test_authored_resume_retains_answers_and_only_pinned_artifacts():
    sandbox = Sandbox()

    async def delegate(task, resume=None, target=sandbox):
        return await target.call(
            "subagent",
            {"task": task, **({"resume": resume} if resume else {})},
            subagent_outcome={"ok": True, "result": {"response": "Authored: " + task}},
        )

    first = await sandbox.call("file_write", {"path": "code.py", "content": "original"})
    await sandbox.call("file_write", {"path": "private", "content": "unshared secret"})
    started = await sandbox.call(
        "subagent",
        {
            "task": "Review",
            "artifacts": [{"path": "code.py", "version": first["version"]}],
        },
        subagent_outcome={"ok": True, "result": {"response": "answer 1"}},
    )
    cid = started["conversationId"]
    await sandbox.call("file_write", {"path": "code.py", "content": "new content"})
    await delegate("Why that choice?", cid)
    history = (await sandbox.call("subagent_get", {"conversationId": cid}))["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[1]["content"] == "answer 1"
    assert "original" in history[0]["content"]
    assert "new content" not in json.dumps(
        history
    ) and "unshared secret" not in json.dumps(history)
    history.clear()
    assert len(sandbox.conversations[cid]["messages"]) == 4
    independent = await delegate("Independent")
    assert sandbox.conversations[independent["conversationId"]]["messages"][0] == {
        "role": "user",
        "content": "Independent",
    }
    restored = Sandbox()
    restored.restore(sandbox.snapshot())
    await delegate("Continue after restore", cid, restored)
    assert len(restored.conversations[cid]["messages"]) == 6
    await restored.call("subagent_close", {"conversationId": cid})
    with pytest.raises(ToolError, match="closed"):
        await delegate("late", cid, restored)
    with pytest.raises(ToolError, match="Unknown conversationId"):
        await delegate("cross sample", cid, Sandbox())
    sandbox.close()
    assert sandbox.conversations == {} and sandbox.artifacts == {}


async def test_artifact_references_survive_edits_deletion_and_reject_conflicts():
    sandbox = Sandbox()
    written = await sandbox.call("file_write", {"path": "x", "content": "first"})
    version = written["version"]
    await sandbox.call(
        "file_patch",
        {"path": "x", "old": "first", "new": "second", "expected_version": version},
    )
    with pytest.raises(ToolError, match="version conflict"):
        await sandbox.call(
            "file_write", {"path": "x", "content": "stale", "expected_version": version}
        )
    assert sandbox.files["/x"] == "second"
    with pytest.raises(ToolError, match="exactly once"):
        await sandbox.call("file_patch", {"path": "x", "old": "missing", "new": "bad"})
    await sandbox.call("file_delete", {"path": "x"})
    assert (await sandbox.call("file_read", {"path": "x", "version": version}))[
        "content"
    ] == "first"
    with pytest.raises(ToolError, match="not found"):
        await sandbox.call("file_read", {"path": "x", "version": "unknown"})
    small = Sandbox(max_bytes=256)
    before = small.snapshot()
    with pytest.raises(ToolError, match="quota"):
        await small.call("file_write", {"path": "x", "content": "a" * 100})
    assert small.files == before["files"] and small.artifacts == before["artifacts"]


async def test_task_dependencies_blockers_and_acceptance_criteria():
    sandbox = Sandbox()
    await sandbox.call(
        "note_save",
        {
            "key": "code",
            "text": "Implement",
            "status": "doing",
            "owner": "conversation-1",
        },
    )
    await sandbox.call(
        "note_save",
        {
            "key": "test",
            "text": "Verify",
            "status": "blocked",
            "depends_on": ["code"],
            "blockers": ["needs source"],
            "acceptance_criteria": ["all tests pass"],
        },
    )
    with pytest.raises(ToolError, match="cycle"):
        await sandbox.call(
            "note_save",
            {
                "key": "code",
                "text": "Implement",
                "status": "doing",
                "depends_on": ["test"],
            },
        )
    with pytest.raises(ToolError, match="unfinished"):
        await sandbox.call(
            "note_save",
            {"key": "test", "text": "Verify", "status": "done", "blockers": []},
        )
    await sandbox.call(
        "note_save", {"key": "code", "text": "Implement", "status": "done"}
    )
    await sandbox.call(
        "note_save",
        {"key": "test", "text": "Verified", "status": "done", "blockers": []},
    )
    assert sandbox.notes["test"]["acceptance_criteria"] == ["all tests pass"]
    assert sandbox.notes["code"]["owner"] == "conversation-1"
    assert (await sandbox.call("runtime_status", {}))["tasks"]["done"] == 2


def test_replay_persistent_lifecycle_and_runtime_status_never_show_future_answers(
    compiler, sample
):
    sample["replay"].update(
        {
            "1": [agent("Implement", "coder", "first result", duration=5)],
            "2": [
                {
                    "op": "tool-call",
                    "tool": "runtime_status",
                    "arguments": {},
                    "window": "status",
                    "content": "x",
                }
            ],
            "8": [
                agent("Clarify", "coder", "second result", resume="coder", duration=3)
            ],
        }
    )
    plan = compiler.compile(sample)
    middle = compiler.frame(plan, 3)["sandbox"]
    assert middle["conversations"]["coder"] == {
        "status": "running",
        "messages": [{"role": "user", "content": "Implement"}],
    }
    assert "first result" not in json.dumps(middle)
    status = plan["tool_calls"][1]["outcome"]["result"]
    assert status["subagents"] == [
        {"conversationId": "coder", "status": "running", "turns": 0}
    ]
    final = compiler.frame(plan, 12)["sandbox"]
    assert final["conversations"]["coder"]["status"] == "ready"
    assert [m["content"] for m in final["conversations"]["coder"]["messages"]] == [
        "Implement",
        "first result",
        "Clarify",
        "second result",
    ]
    # Replaying backwards must restore the prior ready state.
    assert (
        len(compiler.frame(plan, 7)["sandbox"]["conversations"]["coder"]["messages"])
        == 2
    )


def test_resume_is_checked_at_dispatch_and_cannot_invent_or_reuse_identity(
    compiler, sample
):
    sample["replay"].update(
        {
            "1": [agent("first", "coder", duration=10)],
            "2": [agent("too early", "coder", resume="coder", duration=20)],
            "23": [agent("unknown", "missing", resume="missing")],
            "24": [agent("duplicate", "coder")],
            "25": [agent("wrong id", "imposter", resume="coder")],
        }
    )
    plan = compiler.compile(sample)
    outcomes = [c["outcome"] for c in plan["tool_calls"]]
    assert outcomes[0]["ok"]
    assert not any(o["ok"] for o in outcomes[1:])
    assert [
        m["content"]
        for m in compiler.frame(plan, 25)["sandbox"]["conversations"]["coder"][
            "messages"
        ]
    ] == ["first", "answer"]


def test_legacy_outcomes_get_stable_ids_and_recorded_failures_can_be_resumed(
    compiler, sample
):
    event = agent("first", "legacy")
    del event["outcome"]["result"]["conversationId"]
    sample["replay"].update(
        {
            "1": [event],
            "2": [
                {
                    **agent("retry", "conversation-1", resume="conversation-1"),
                    "outcome": {"ok": False, "error": "provider failed"},
                }
            ],
            "4": [agent("recover", "conversation-1", resume="conversation-1")],
        }
    )
    plan = compiler.compile(sample)
    assert (
        plan["tool_calls"][0]["outcome"]["result"]["conversationId"] == "conversation-1"
    )
    assert (
        compiler.frame(plan, 3)["sandbox"]["conversations"]["conversation-1"]["status"]
        == "failed"
    )
    assert (
        compiler.frame(plan, 5)["sandbox"]["conversations"]["conversation-1"]["status"]
        == "ready"
    )


def test_persistent_fixture_keeps_implementation_out_of_coordinator_tokens(compiler):
    source = json.loads(
        (Path(__file__).parents[1] / "examples/persistent-agents.json").read_text()
    )
    plan = compiler.compile(source)
    frame = compiler.frame(plan, plan["max_tick"])
    assert frame["sandbox"]["files"]["/math.py"].startswith("def double")
    assert all(n["status"] == "done" for n in frame["sandbox"]["notes"].values())
    assert len(frame["sandbox"]["conversations"]["coder"]["messages"]) == 4
    review = frame["sandbox"]["conversations"]["reviewer"]["messages"][0]["content"]
    assert "def double" in review
    assert not any("def double" in w["text"] for w in frame["windows"].values())
    assert all(w["close"] is not None for w in plan["windows"])


def test_artifacts_must_exist_at_dispatch_and_remain_pinned_during_work(
    compiler, sample
):
    version = "sha256:" + hashlib.sha256(b"original").hexdigest()
    reference = {"path": "x", "version": version}
    sample["replay"].update(
        {
            "1": [
                agent("Review future file", "early", duration=20, artifacts=[reference])
            ],
            "2": [external("file_write", {"path": "x", "content": "original"})],
            "3": [
                agent(
                    "Review current file",
                    "reviewer",
                    duration=10,
                    artifacts=[reference],
                )
            ],
            "4": [external("file_write", {"path": "x", "content": "changed"})],
        }
    )
    plan = compiler.compile(sample)
    assert not plan["tool_calls"][0]["outcome"]["ok"]
    assert plan["tool_calls"][2]["outcome"]["ok"]
    state = compiler.frame(plan, 21)["sandbox"]
    assert "early" not in state["conversations"]
    assert state["files"]["/x"] == "changed"
    messages = json.dumps(state["conversations"]["reviewer"]["messages"])
    assert "original" in messages and "changed" not in messages


@pytest.mark.parametrize("response", [None, "", "a" * 200_001])
async def test_invalid_authored_responses_leave_state_unchanged(response):
    sandbox = Sandbox()
    with pytest.raises(ToolError):
        await sandbox.call(
            "subagent",
            {"task": "Implement"},
            subagent_outcome={"ok": True, "result": {"response": response}},
        )
    assert not sandbox.conversations and sandbox.calls == 0
