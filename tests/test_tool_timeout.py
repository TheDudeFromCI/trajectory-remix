import multiprocessing
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType
from dataclasses import replace

import pytest

from trajectory_remix.compiler import CompileError
from trajectory_remix.tools import (
    ExecutionContext,
    ToolError,
    ToolRegistry,
    WindowProperties,
)
from trajectory_remix.tools import _timeout


def registry_with(run):
    registry = ToolRegistry()
    module = ModuleType("hanging")
    module.name = "hanging"
    module.schema = {"type": "object"}
    module.run = run
    registry.register(module)
    return registry


def hang(args, state, windows):
    while True:
        pass


def test_default_timeout_is_ten_seconds():
    assert _timeout.RUN_TIMEOUT_SECONDS == 10


def test_hanging_invocation_reports_tool_tick_and_event(compiler, sample, monkeypatch):
    monkeypatch.setattr(_timeout, "RUN_TIMEOUT_SECONDS", 0.05)
    compiler.tools = registry_with(hang)
    sample["replay"]["1"] = [
        {
            "op": "tool-call",
            "tool": "hanging",
            "arguments": {},
            "window": "tool",
            "content": "x",
        }
    ]
    started = time.monotonic()
    with pytest.raises(
        CompileError, match="hanging exceeded 0.05 seconds at tick 2"
    ) as error:
        compiler.compile(sample)
    assert error.value.path == "replay[1][0]"
    assert time.monotonic() - started < 2


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="POSIX timer restoration")
def test_alarm_is_restored_after_timeout_and_success(monkeypatch):
    monkeypatch.setattr(_timeout, "RUN_TIMEOUT_SECONDS", 0.02)
    previous_handler = signal.getsignal(signal.SIGALRM)
    registry = registry_with(hang)
    with pytest.raises(ToolError, match="exceeded"):
        registry.run_tick("hanging", {}, {}, [], ExecutionContext())
    assert signal.getsignal(signal.SIGALRM) == previous_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    registry.tools["hanging"] = replace(
        registry.get("hanging"), run=lambda args, state, windows: ("done", True, False)
    )
    assert registry.run_tick("hanging", {}, {}, [], ExecutionContext()) == (
        "done",
        True,
        False,
    )
    assert signal.getsignal(signal.SIGALRM) == previous_handler


def test_worker_timeout_terminates_and_discards_partial_mutations(
    tmp_path, monkeypatch
):
    path = tmp_path / "hanging.py"
    path.write_text("""import time
name = "hanging"
schema = {"type": "object"}
def run(args, state, windows):
    state["partial"] = True
    windows[0].content = "partial"
    time.sleep(60)
    return "late", True, True
""")
    registry = ToolRegistry(tmp_path)
    monkeypatch.setattr(_timeout, "RUN_TIMEOUT_SECONDS", 0.5)
    children = {child.pid for child in multiprocessing.active_children()}
    state, windows = {"original": True}, [WindowProperties("a", "document", "original")]
    started = time.monotonic()
    with pytest.raises(ToolError, match="exceeded 0.5 seconds at tick 7"):
        registry.run_tick("hanging", {}, state, windows, ExecutionContext(tick=7))
    assert time.monotonic() - started < 3
    assert state == {"original": True} and windows[0].content == "original"
    assert {child.pid for child in multiprocessing.active_children()} == children


def test_threaded_execution_uses_worker_and_preserves_state_and_windows():
    def run(args, state, windows):
        state["result"] = 1
        windows[0].content += " updated"
        return "done", True, True

    registry = registry_with(run)
    state, windows = {}, [WindowProperties("a", "document", "text")]
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(
            registry.run_tick, "hanging", {}, state, windows, ExecutionContext()
        ).result(timeout=10) == ("done", True, True)
    assert state == {"result": 1} and windows[0].content == "text updated"


def test_custom_worker_preserves_execution_context(tmp_path):
    (
        tmp_path / "context_tool.py"
    ).write_text("""from trajectory_remix.tools import execution_context
name = "context_tool"
schema = {"type": "object"}
def run(args, state, windows):
    state["tick"] = execution_context.get().tick
    return "done", True, True
""")
    registry = ToolRegistry(tmp_path)
    state = {}
    assert registry.run_tick(
        "context_tool", {}, state, [], ExecutionContext(tick=8)
    ) == ("done", True, True)
    assert state == {"tick": 8}
