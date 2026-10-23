import asyncio
import copy
import json
import uuid

import pytest

from trajectory_remix.execution import receipt
from trajectory_remix.tool_sessions import ToolSessions
from trajectory_remix.tools import ToolError


class Executor:
    async def run(self, files, argv, timeout_seconds=10):
        result = {
            "executionId": "execution-" + uuid.uuid4().hex,
            "backend": "docker",
            "image": "sha256:" + "a" * 64,
            "argv": argv,
            "inputs": [{k: f[k] for k in ("path", "version")} for f in files],
            "exit_code": 0,
            "timed_out": False,
            "status": "passed",
            "stdout": "",
            "stderr": "Ran 1 test\nOK\n",
            "truncated": False,
            "elapsed_ms": 10,
        }
        result["receipt_sha256"] = receipt(result)
        return result


async def command(studio, operation, **args):
    response = await studio[1].post(
        "/api/mcp/call", json={"name": operation, "arguments": args}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def root(studio, name, args):
    return (
        await command(
            studio, "call_simulated_tool", session_id="live", name=name, arguments=args
        )
    )["interaction"]["outcome"]


async def bind(studio):
    studio[0].state.commands.executor = Executor()
    await command(
        studio,
        "create_tool_session",
        session_id="live",
        external_agents=True,
        enable_execution=True,
        trajectory_id="test",
        max_calls=128,
    )


async def launch(studio, task="Implement", **extra):
    return (
        await root(studio, "subagent", {"task": task, "background": True, **extra})
    )["result"]


async def worker(studio, job, name, args, identity="coder"):
    return (
        await command(
            studio,
            "call_subagent_tool",
            session_id="live",
            job_id=job["jobId"],
            worker_id=identity,
            name=name,
            arguments=args,
        )
    )["outcome"]


async def claim(studio, job, identity="coder"):
    return await command(
        studio,
        "claim_subagent_job",
        session_id="live",
        job_id=job["jobId"],
        worker_id=identity,
    )


async def complete(studio, job, response="Implemented", identity="coder"):
    return await command(
        studio,
        "complete_subagent_job",
        session_id="live",
        job_id=job["jobId"],
        worker_id=identity,
        response=response,
    )


async def test_bound_worker_files_evidence_followup_and_finalization(studio, compiler):
    await bind(studio)
    job = await launch(studio)
    claimed = await claim(studio, job)
    assert claimed["messages"] == [{"role": "user", "content": "Implement"}]
    await worker(
        studio,
        job,
        "file_write",
        {"path": "code.py", "content": "print('worker implementation')"},
    )
    assert not (await command(studio, "get_tool_session", session_id="live"))["state"][
        "files"
    ]
    evidence = await worker(studio, job, "python_run", {"argv": ["code.py"]})
    finished = await complete(studio, job)
    assert finished["status"] == "completed"
    eid = evidence["result"]["executionId"]
    note = await root(
        studio,
        "note_save",
        {
            "key": "verify",
            "text": "Test",
            "status": "done",
            "requires_evidence": True,
            "evidence": [eid],
        },
    )
    assert note["ok"]
    followup = await launch(studio, "Explain edge cases", resume=job["conversationId"])
    context = await claim(studio, followup)
    assert context["messages"][-2:] == [
        {"role": "assistant", "content": "Implemented"},
        {"role": "user", "content": "Explain edge cases"},
    ]
    assert (await worker(studio, followup, "file_read", {"path": "code.py"}))["result"][
        "content"
    ].startswith("print")
    await complete(studio, followup, "Explained")
    await command(studio, "finalize_trajectory", sample_id="test")
    report = await command(studio, "validate_trajectory", sample_id="test")
    assert report["valid"] and not report["diagnostics"]
    source = (await command(studio, "get_trajectory", sample_id="test"))["sample"]
    plan = compiler.compile(source)
    final = compiler.frame(plan, plan["max_tick"])
    assert final["sandbox"]["executions"][eid]["status"] == "passed"
    assert "worker implementation" not in json.dumps(final["windows"])
    write = next(c for c in plan["tool_calls"] if c["tool"] == "file_write")
    assert write["invisible"] and write["source"] == "coder"
    assert not compiler.frame(plan, write["start"] - 1)["sandbox"]["files"]
    notices = [
        e
        for events in source["replay"].values()
        for e in events
        if e["op"] == "append" and "<subagent_result>" in e.get("content", "")
    ]
    assert len(notices) == 2
    assert all(e["loss"] == {"mask": "exclude"} for e in notices)
    assert "Implemented" in notices[0]["content"]
    assert "worker implementation" not in json.dumps(notices)


async def test_bound_runtime_rejects_source_changes_without_committing_state(studio):
    await bind(studio)
    before = (await command(studio, "get_tool_session", session_id="live"))["state"]
    await command(
        studio,
        "update_trajectory_metadata",
        sample_id="test",
        patch={"metadata": {"changed": True}},
    )
    response = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "call_simulated_tool",
            "arguments": {
                "session_id": "live",
                "name": "file_write",
                "arguments": {"path": "uncommitted", "content": "x"},
            },
        },
    )
    assert response.status_code == 409 and "rebind" in response.text
    assert (await command(studio, "get_tool_session", session_id="live"))[
        "state"
    ] == before


async def test_conflict_while_waiting_for_parent_lock_terminates_job():
    manager = ToolSessions()
    manager.create("live", {"external_agents": True})
    try:
        job = (
            await manager.call(
                "live", "subagent", {"task": "Write", "background": True}
            )
        )["interaction"]["outcome"]["result"]
        jid = job["jobId"]
        await manager.agents.claim("live", jid, "worker")
        await manager.agents.tool(
            "live", jid, "worker", "file_write", {"path": "same", "content": "loser"}
        )
        async with manager.get("live").lock:
            pending = asyncio.create_task(
                manager.agents.complete("live", jid, "worker", "Done")
            )
            await asyncio.sleep(0)
            await manager.get("live").sandbox.call(
                "file_write", {"path": "same", "content": "winner"}
            )
        result = await pending
        assert result["status"] == "failed" and "conflict" in result["outcome"]["error"]
        assert manager.get("live").sandbox.files == {"/same": "winner"}
    finally:
        await manager.aclose()


async def test_wait_releases_session_and_cancel_rejects_late_publication(studio):
    await bind(studio)
    job = await launch(studio)
    waiting = asyncio.create_task(
        root(studio, "subagent_wait", {"jobId": job["jobId"], "timeout_seconds": 1})
    )
    await asyncio.sleep(0)
    await claim(studio, job)
    await asyncio.sleep(0.02)
    await worker(studio, job, "file_write", {"path": "x", "content": "published"})
    await complete(studio, job)
    assert (await waiting)["result"]["status"] == "completed"
    log = (await command(studio, "get_tool_session", session_id="live"))["interactions"]
    assert next(e for e in log if e["name"] == "subagent_wait")["elapsed_ms"] >= 15
    cancelled = await launch(studio, "Cancelled work")
    await claim(studio, cancelled)
    await worker(
        studio, cancelled, "file_write", {"path": "late", "content": "discard"}
    )
    assert (await root(studio, "subagent_cancel", {"jobId": cancelled["jobId"]}))[
        "result"
    ]["status"] == "cancelled"
    rejected = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "complete_subagent_job",
            "arguments": {
                "session_id": "live",
                "job_id": cancelled["jobId"],
                "worker_id": "coder",
                "response": "too late",
            },
        },
    )
    assert rejected.status_code == 422
    assert (
        "/late"
        not in (await command(studio, "get_tool_session", session_id="live"))["state"][
            "files"
        ]
    )


async def test_parallel_writers_conflict_and_wrong_resume_worker_is_rejected(studio):
    await bind(studio)
    first, second = await launch(studio, "First"), await launch(studio, "Second")
    await claim(studio, first, "one")
    await claim(studio, second, "two")
    await worker(studio, first, "file_write", {"path": "same", "content": "one"}, "one")
    await worker(
        studio, second, "file_write", {"path": "same", "content": "two"}, "two"
    )
    await complete(studio, first, identity="one")
    failed = await complete(studio, second, identity="two")
    assert failed["status"] == "failed" and "conflict" in failed["outcome"]["error"]
    assert (await command(studio, "get_tool_session", session_id="live"))["state"][
        "files"
    ] == {"/same": "one"}
    resumed = await launch(studio, "Follow up", resume=first["conversationId"])
    rejected = await studio[1].post(
        "/api/mcp/call",
        json={
            "name": "claim_subagent_job",
            "arguments": {
                "session_id": "live",
                "job_id": resumed["jobId"],
                "worker_id": "imposter",
            },
        },
    )
    assert rejected.status_code == 422


async def test_background_workers_share_refs_and_retain_private_files():
    manager = ToolSessions()
    manager.create("live", {"external_agents": True})
    try:
        shared = await manager.call(
            "live", "file_write", {"path": "shared.py", "content": "source body"}
        )
        version = shared["interaction"]["outcome"]["result"]["version"]
        await manager.call(
            "live", "file_write", {"path": "secret", "content": "parent only"}
        )
        job = (
            await manager.call(
                "live",
                "subagent",
                {
                    "task": "Inspect",
                    "background": True,
                    "artifacts": [{"path": "/shared.py", "version": version}],
                },
            )
        )["interaction"]["outcome"]["result"]
        jid = job["jobId"]
        claimed = await manager.agents.claim("live", jid, "worker")
        assert "source body" not in json.dumps(claimed["messages"])
        assert claimed["files"] == [{"path": "/shared.py", "version": version}]
        read = await manager.agents.tool(
            "live", jid, "worker", "file_read", {"path": "/shared.py"}
        )
        assert read["outcome"]["result"]["content"] == "source body"
        await manager.agents.tool(
            "live",
            jid,
            "worker",
            "file_write",
            {"path": "private", "content": "remember"},
        )
        await manager.agents.complete("live", jid, "worker", "Inspected")
        resumed = (
            await manager.call(
                "live",
                "subagent",
                {
                    "task": "Clarify",
                    "resume": job["conversationId"],
                    "background": True,
                },
            )
        )["interaction"]["outcome"]["result"]
        context = await manager.agents.claim("live", resumed["jobId"], "worker")
        assert {ref["path"] for ref in context["files"]} == {"/shared.py", "/private"}
        assert [m["role"] for m in context["messages"]] == ["user", "assistant", "user"]
    finally:
        await manager.aclose()


async def test_native_jobs_really_overlap_and_tool_calls_publish():
    entered = []
    release = asyncio.Event()

    async def broker(messages, depth, tools):
        entered.append(messages[-1]["content"])
        await release.wait()
        await tools(
            "file_write", {"path": messages[-1]["content"], "content": "from worker"}
        )
        return "Done"

    manager = ToolSessions()
    manager.create("live", {"broker": broker})
    try:
        jobs = [
            (
                await manager.call(
                    "live", "subagent", {"task": name, "background": True}
                )
            )["interaction"]["outcome"]["result"]
            for name in ("one", "two")
        ]
        for _ in range(10):
            if len(entered) == 2:
                break
            await asyncio.sleep(0)
        assert set(entered) == {"one", "two"}
        release.set()
        for job in jobs:
            result = await manager.call(
                "live", "subagent_wait", {"jobId": job["jobId"], "timeout_seconds": 1}
            )
            assert result["interaction"]["outcome"]["result"]["status"] == "completed"
        assert manager.get("live").sandbox.files == {
            "/one": "from worker",
            "/two": "from worker",
        }
        identities = {
            c["worker_id"] for c in manager.get("live").sandbox.conversations.values()
        }
        assert len(identities) == 2
    finally:
        await manager.aclose()


async def test_cancelling_external_work_stops_inflight_execution():
    entered, stopped = asyncio.Event(), asyncio.Event()

    class BlockingExecutor:
        async def run(self, *args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    manager = ToolSessions()
    manager.create("live", {"external_agents": True, "executor": BlockingExecutor()})
    try:
        job = (
            await manager.call("live", "subagent", {"task": "Run", "background": True})
        )["interaction"]["outcome"]["result"]
        jid = job["jobId"]
        await manager.agents.claim("live", jid, "worker")
        await manager.agents.tool(
            "live",
            jid,
            "worker",
            "file_write",
            {"path": "x.py", "content": "while True: pass"},
        )
        pending = asyncio.create_task(
            manager.agents.tool("live", jid, "worker", "python_run", {"argv": ["x.py"]})
        )
        await entered.wait()
        await manager.call("live", "subagent_cancel", {"jobId": jid})
        with pytest.raises(ToolError, match="cancelled"):
            await pending
        assert stopped.is_set() and not manager.get("live").sandbox.executions
    finally:
        await manager.aclose()


async def test_native_configured_generation_uses_background_workers(compiler):
    from trajectory_remix.llm import SampleBuilder
    from test_generation import minimal_config

    config = minimal_config()
    config["tasks"] = [config["tasks"][1]]

    class Client:
        def __init__(self):
            self.root_turn = 0

        async def complete(self, endpoint, messages, tools=None):
            root_tools = tools and any(
                t["function"]["name"] == "subagent" for t in tools
            )

            def toolcall(identifier, name, args):
                return {
                    "id": identifier,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }

            if root_tools:
                self.root_turn += 1
                if self.root_turn == 1:
                    return {
                        "content": None,
                        "tool_calls": [
                            toolcall(
                                "start-one",
                                "subagent",
                                {"task": "one.py", "background": True},
                            ),
                            toolcall(
                                "start-two",
                                "subagent",
                                {"task": "two.py", "background": True},
                            ),
                        ],
                    }
                if self.root_turn == 2:
                    jid = json.loads(messages[-2]["content"])["result"]["jobId"]
                    return {
                        "content": None,
                        "tool_calls": [
                            toolcall(
                                "wait-one",
                                "subagent_wait",
                                {"jobId": jid, "timeout_seconds": 1},
                            )
                        ],
                    }
                if self.root_turn == 3:
                    jid = json.loads(
                        next(
                            m["content"]
                            for m in messages
                            if m.get("tool_call_id") == "start-two"
                        )
                    )["result"]["jobId"]
                    return {
                        "content": None,
                        "tool_calls": [
                            toolcall(
                                "wait-two",
                                "subagent_wait",
                                {"jobId": jid, "timeout_seconds": 1},
                            )
                        ],
                    }
                return {"content": "Coordinated both workers."}
            if messages[-1]["role"] == "user":
                return {
                    "content": None,
                    "tool_calls": [
                        toolcall(
                            "write",
                            "file_write",
                            {
                                "path": messages[-1]["content"],
                                "content": "print('worker')",
                            },
                        )
                    ],
                }
            return {"content": "Implemented"}

    generated = await SampleBuilder(compiler, config, Client()).generate()
    plan = compiler.compile(generated)
    final = compiler.frame(plan, plan["max_tick"])["sandbox"]
    assert final["files"] == {
        "/one.py": "print('worker')",
        "/two.py": "print('worker')",
    }
    assert all(j["status"] == "completed" for j in final["jobs"].values())
    assert not any(
        c["tool"] == "file_write" and not c.get("invisible") for c in plan["tool_calls"]
    )
