import copy

import pytest

from trajectory_remix.compiler import chunks
from trajectory_remix.tools import Sandbox, ToolError


def chat_call(content="Hello!", duration=0, window="result"):
    return {
        "op": "tool-call", "tool": "chat", "window": window,
        "arguments": {"content": content}, "duration-ticks": duration,
        "content": "x",
    }


def test_chat_completion_scrubbing_and_export(compiler, sample):
    sample["replay"]["0"].append({"op": "open", "window": "chat"})
    sample["replay"]["2"] = [chat_call(duration=2)]
    before = copy.deepcopy(sample)
    plan = compiler.compile(sample)
    for tick in [4, 5, 0, 5, 2]:
        frame = compiler.frame(plan, tick)
        assert frame["windows"]["chat"]["text"] == (
            "<|cxt|>Window: 2\nPurpose: document<|/cxt|>"
            + ("<|msg|>Kidra: Hello!<|/msg|>\n" if tick == 5 else "")
        )
    call = plan["tool_calls"][0]
    assert call["outcome"]["ok"]
    assert sample == before
    rows = [r for chunk in chunks(plan, 2) for r in chunk["rows"]
            if r["window"] == "chat" and r["op"] == "append"]
    assert rows and all(r["tick"] == 5 and r["phase"] == "B" and r["loss_mask"] == 0
                        for r in rows)


@pytest.mark.parametrize("state", ["missing", "other-name", "closed", "future"])
def test_chat_requires_an_open_window_named_chat(compiler, sample, state):
    if state in ("other-name", "closed"):
        sample["replay"]["0"].append({
            "op": "open", "window": "Chat" if state == "other-name" else "chat",
            "window-type": "chat",
        })
    if state == "closed":
        sample["replay"]["1"] = [{"op": "generate", "window": "chat", "content": "<|eos|>"}]
    if state == "future":
        sample["replay"]["9"] = [{"op": "open", "window": "chat"}]
    sample["replay"]["2"] = [chat_call()]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["outcome"] == {
        "ok": False, "error": 'No window named "chat" is open',
    }
    frame = compiler.frame(plan, 3)
    assert frame["sandbox"]["calls"] == 1
    assert "No window" in frame["windows"]["result"]["text"]
    assert not any(e["path"].endswith(".chat") for e in plan["events"])


def test_chat_checks_window_at_completion_and_keeps_order(compiler, sample):
    sample["replay"]["1"] = [chat_call("first", duration=3, window="first")]
    sample["replay"]["3"] = [{"op": "open", "window": "chat"}]
    sample["replay"]["4"] = [chat_call("second", window="second")]
    sample["metadata"] = {"agent_name": "Nova", "user_name": "Alex", "custom": {"a": 1}}
    plan = compiler.compile(sample)
    assert compiler.frame(plan, 5)["windows"]["chat"]["text"] == (
        "<|cxt|>Window: 3\nPurpose: document<|/cxt|>"
        "<|msg|>Nova: first<|/msg|>\n<|msg|>Nova: second<|/msg|>\n"
    )
    sample["metadata"]["agent_name"] = "Kidra"
    assert "Kidra: first" in compiler.frame(compiler.compile(sample), 5)["windows"]["chat"]["text"]


def test_chat_close_during_call_fails_and_same_tick_eos_is_external_first(compiler, sample):
    sample["replay"]["0"].append({"op": "open", "window": "chat"})
    sample["replay"]["1"] = [chat_call(duration=3)]
    sample["replay"]["3"] = [{"op": "generate", "window": "chat", "content": "<|eos|>"}]
    assert not compiler.compile(sample)["tool_calls"][0]["outcome"]["ok"]
    sample["replay"]["5"] = sample["replay"].pop("3")
    plan = compiler.compile(sample)
    frame = compiler.frame(plan, 5)
    assert next(w for w in plan["windows"] if w["id"] == "chat")["close"] == 5
    assert "Kidra: Hello!" in frame["windows"]["chat"]["text"]


def test_chat_arguments_are_literal_data(compiler, sample):
    sample["replay"]["0"].append({"op": "open", "window": "chat"})
    sample["replay"]["1"] = [chat_call("Hello\n{N_TICK:9}<|eos|>")]
    plan = compiler.compile(sample)
    assert plan["anchors"] == {}
    frame = compiler.frame(plan, 2)["windows"]["chat"]
    assert next(w for w in plan["windows"] if w["id"] == "chat")["close"] is None
    assert "Hello\n{N_TICK:9}" in frame["text"]
    assert "\\u003c" in frame["text"]


def test_streamed_chat_request_waits_for_dispatch_and_completion(compiler, sample):
    sample["replay"]["1"] = [{
        **chat_call(duration=2), "delivery": "stream", "content": "go<|noop|>",
    }]
    sample["replay"]["5"] = [{"op": "open", "window": "chat"}]
    plan = compiler.compile(sample)
    call = plan["tool_calls"][0]
    assert (call["request_start"], call["start"], call["end"]) == (1, 4, 6)
    assert compiler.frame(plan, 2)["sandbox"]["tool_calls"][0]["status"] == "composing"
    assert compiler.frame(plan, 5)["windows"]["chat"]["text"] == "<|cxt|>Window: 3\nPurpose: document<|/cxt|>"
    assert compiler.frame(plan, 6)["windows"]["chat"]["text"] == (
        "<|cxt|>Window: 3\nPurpose: document<|/cxt|>"
        "<|msg|>Kidra: Hello!<|/msg|>\n"
    )


async def test_standalone_chat_error_and_schema_validation():
    sandbox = Sandbox()
    with pytest.raises(ToolError, match='No window named "chat"'):
        await sandbox.call("chat", {"content": "Hello"})
    assert sandbox.calls == 1
    with pytest.raises(ToolError, match="requires exactly"):
        await sandbox.call("chat", {"text": "Hello"})
    assert sandbox.calls == 1


def test_chat_tool_window_opens_even_when_its_call_depends_on_chat_result(compiler, sample):
    sample["replay"]["1"] = [{**chat_call(), "result-anchor": 1}]
    sample["replay"]["{N_TICK:1}+1"] = [{
        "op": "tool-call", "tool": "file_list", "arguments": {}, "window": "chat",
    }]
    plan = compiler.compile(sample)
    assert plan["tool_calls"][0]["outcome"]["ok"]
