import asyncio
import copy
import os

import pytest

from trajectory_remix.compiler import CompileError
from trajectory_remix.execution import (
    DockerExecutor,
    OUTPUT_LIMIT,
    receipt,
    validate_execution,
)
from trajectory_remix.tools import Sandbox, ToolError
from test_background_runtime import Executor


async def test_recorded_execution_replays_offline_and_detects_changed_inputs(
    compiler, sample, monkeypatch
):
    sandbox = Sandbox(executor=Executor())
    await sandbox.call("file_write", {"path": "code.py", "content": "print(1)"})
    result = await sandbox.call("python_run", {"argv": ["code.py"]})
    sample["replay"].update(
        {
            "1": [
                {
                    "op": "invisible-tool-call",
                    "tool": "file_write",
                    "arguments": {"path": "code.py", "content": "print(1)"},
                }
            ],
            "2": [
                {
                    "op": "tool-call",
                    "window": "run",
                    "tool": "python_run",
                    "arguments": {"argv": ["code.py"]},
                    "outcome": {"ok": True, "result": result},
                    "duration-ticks": 3,
                }
            ],
        }
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("Replay must not execute Docker")

    monkeypatch.setattr(DockerExecutor, "run", forbidden)
    plan = compiler.compile(sample)
    assert not compiler.frame(plan, 4)["sandbox"]["executions"]
    assert (
        compiler.frame(plan, 5)["sandbox"]["executions"][result["executionId"]][
            "status"
        ]
        == "passed"
    )
    changed = copy.deepcopy(sample)
    changed["replay"]["1"][0]["arguments"]["content"] = "print(2)"
    assert not compiler.compile(changed)["tool_calls"][-1]["outcome"]["ok"]
    result["stdout"] = "forged"
    with pytest.raises(ToolError, match="checksum"):
        validate_execution(result, sandbox)


async def test_task_evidence_rejects_failed_missing_and_stale_receipts():
    sandbox = Sandbox(executor=Executor())
    await sandbox.call("file_write", {"path": "code.py", "content": "print(1)"})
    run = await sandbox.call("python_run", {"argv": ["code.py"]})
    args = {
        "key": "tests",
        "text": "Check",
        "status": "done",
        "requires_evidence": True,
    }
    with pytest.raises(ToolError, match="requires execution"):
        await sandbox.call("note_save", args)
    with pytest.raises(ToolError, match="passing executions"):
        await sandbox.call("note_save", {**args, "evidence": ["missing"]})
    await sandbox.call("note_save", {**args, "evidence": [run["executionId"]]})
    await sandbox.call("file_write", {"path": "code.py", "content": "print(2)"})
    with pytest.raises(ToolError, match="stale"):
        await sandbox.call("note_save", args)


@pytest.mark.parametrize(
    "changes",
    [
        {"exit_code": None},
        {"status": "failed"},
        {"timed_out": True},
        {"argv": []},
        {"truncated": "false"},
    ],
)
async def test_receipts_reject_inconsistent_metadata_even_with_valid_checksum(changes):
    sandbox = Sandbox(executor=Executor())
    await sandbox.call("file_write", {"path": "x.py", "content": "print(1)"})
    run = await sandbox.call("python_run", {"argv": ["x.py"]})
    run.update(changes)
    run["receipt_sha256"] = receipt(run)
    with pytest.raises(ToolError):
        validate_execution(run, sandbox)


@pytest.mark.parametrize(
    "extra",
    [
        {"argv": "bad"},
        {"timeout_seconds": 31},
        {"timeout_seconds": True},
        {"files": [{"path": "x"}]},
    ],
)
async def test_execution_argument_boundaries(extra):
    with pytest.raises(ToolError):
        await Sandbox(executor=Executor()).call(
            "python_run", {"argv": ["x.py"], **extra}
        )


docker = pytest.mark.skipif(
    os.environ.get("REMIX_TEST_DOCKER") != "1",
    reason="Opt-in real Docker checks: REMIX_TEST_DOCKER=1",
)


@docker
async def test_real_docker_isolation_and_unicode(monkeypatch):
    monkeypatch.setenv("REMIX_HOST_TEST_SECRET", "host-only-secret")
    code = """import os, pathlib, socket
assert 'REMIX_HOST_TEST_SECRET' not in os.environ
assert not pathlib.Path('/workspace/trajectory-remix/pyproject.toml').exists()
try:
    pathlib.Path('/workspace/probe.py').write_text('modified')
except OSError:
    pass
else:
    raise AssertionError('input mount writable')
pathlib.Path('/tmp/probe').write_text('temporary')
s = socket.socket()
s.settimeout(.2)
try:
    s.connect(('127.0.0.1', 8766))
except OSError:
    pass
else:
    raise AssertionError('host network accessible')
print('isolation passed: café')
"""
    sandbox = Sandbox(executor=DockerExecutor())
    await sandbox.call("file_write", {"path": "probe.py", "content": code})
    run = await sandbox.call("python_run", {"argv": ["probe.py"]})
    assert run["status"] == "passed", run["stderr"]
    assert "café" in run["stdout"]
    assert sandbox.files["/probe.py"] == code


@docker
async def test_real_docker_timeout_output_limits_and_cleanup():
    sandbox = Sandbox(executor=DockerExecutor())
    await sandbox.call(
        "file_write", {"path": "large.py", "content": "print('x' * 200000)"}
    )
    output = await sandbox.call("python_run", {"argv": ["large.py"]})
    assert output["status"] == "passed" and output["truncated"]
    assert len(output["stdout"].encode()) <= OUTPUT_LIMIT
    await sandbox.call("file_write", {"path": "loop.py", "content": "while True: pass"})
    timeout = await sandbox.call(
        "python_run", {"argv": ["loop.py"], "timeout_seconds": 1}
    )
    assert timeout["status"] == "timed_out" and timeout["exit_code"] is None
    process = await sandbox.executor.command(
        "ps", "-q", "--filter", "name=" + timeout["container_id"]
    )
    stdout, _ = await process.communicate()
    assert not stdout
