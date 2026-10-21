import asyncio
import copy
import json

import httpx
import pytest

from trajectory_remix.llm import (
    LLMClient,
    SampleBuilder,
    default_config,
    selected_windows,
    validate_config,
)
from trajectory_remix.runner import Runner
from trajectory_remix.tools import Sandbox


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def complete(self, endpoint, messages, tools=None):
        self.messages.append(copy.deepcopy(messages))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, dict) else {"content": value}


def minimal_config():
    c = default_config()
    c["tasks"] = [c["tasks"][1], c["tasks"][2], c["tasks"][-1]]
    c["automatic"].update(
        max_samples=1, autosave_every=1, regenerations=1, delay_seconds=0.1
    )
    return c


async def test_tool_grounded_generation_and_validation(compiler):
    fake = FakeClient(
        [
            "Write a note.",
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "note_save",
                            "arguments": json.dumps(
                                {"key": "n", "text": "check", "status": "todo"}
                            ),
                        },
                    }
                ],
            },
            "Saved the note.",
            '{"valid":true,"feedback":[]}',
        ]
    )
    c = minimal_config()
    b = SampleBuilder(compiler, c, fake)
    sample = await b.generate()
    report = await b.validators(sample)
    assert report["valid"]
    events = [e for a in sample["replay"].values() for e in a]
    assert any(
        e["op"] == "append" and e.get("source") == "tool" and "saved" in e["content"]
        for e in events
    )
    assert any(m["role"] == "tool" for m in fake.messages[2])
    compiler.compile(sample)


async def test_regeneration_uses_validator_feedback_and_checkpoints(compiler, tmp_path):
    c = minimal_config()
    c["tasks"] = [c["tasks"][0], c["tasks"][-1]]
    fake = FakeClient(
        [
            "Bad request",
            '{"valid":false,"feedback":["missing details"]}',
            "Better request",
            '{"valid":true,"feedback":[]}',
        ]
    )
    state = {"samples": [], "config": c, "revision": 0}
    runner = Runner(state, compiler, tmp_path / "auto.kidra-dataset", fake)
    runner.start(single=True)
    await runner.job
    assert runner.status["accepted"] == 1
    assert len(state["samples"]) == 1
    assert "missing details" in fake.messages[2][0]["content"]
    data = json.loads((tmp_path / "auto.kidra-dataset").read_text())
    assert len(data["samples"]) == 1


async def test_invalid_validator_is_failure(compiler):
    c = minimal_config()
    fake = FakeClient(['{"valid":"yes","feedback":[]}'])
    b = SampleBuilder(compiler, c, fake)
    from trajectory_remix.dataset import new_sample

    with pytest.raises(ValueError, match="Validator must"):
        await b.validators(new_sample())


async def test_no_validator_does_not_silently_pass(compiler, sample):
    c = minimal_config()
    c["tasks"] = c["tasks"][:1]
    with pytest.raises(ValueError, match="no validator"):
        await SampleBuilder(compiler, c, FakeClient([])).validators(sample)


async def test_crash_limits_and_skip(compiler, tmp_path):
    c = minimal_config()
    c["tasks"] = c["tasks"][:1]
    c["automatic"].update(
        validate=False,
        max_samples=3,
        max_attempts=4,
        max_consecutive_failures=2,
        failure_action="skip",
        regenerations=0,
    )
    fake = FakeClient([ValueError("crashed")] * 4)
    state = {"samples": [], "config": c, "revision": 0}
    runner = Runner(state, compiler, tmp_path / "a", fake)
    runner.start()
    await runner.job
    assert runner.status["attempts"] == 2
    assert runner.status["failures"] == 2
    assert state["samples"] == []


async def test_cancellation_destroys_sandbox(compiler, tmp_path, monkeypatch):
    sandboxes = []
    original = Sandbox.__init__

    def init(self, *a, **kw):
        original(self, *a, **kw)
        sandboxes.append(self)

    monkeypatch.setattr(Sandbox, "__init__", init)
    entered = asyncio.Event()

    class Blocked:
        async def complete(self, *a, **kw):
            entered.set()
            await asyncio.Event().wait()

    c = minimal_config()
    state = {"samples": [], "config": c, "revision": 0}
    r = Runner(state, compiler, tmp_path / "a", Blocked())
    r.start()
    await asyncio.wait_for(entered.wait(), 2)
    await r.stop()
    assert not r.status["running"]
    assert all(s.closed for s in sandboxes)


async def test_run_task_on_seed(compiler, sample, tmp_path):
    c = minimal_config()
    c["automatic"]["validate"] = False
    state = {"samples": [sample], "config": c, "revision": 0}
    r = Runner(
        state, compiler, tmp_path / "a", FakeClient(["A generated user request."])
    )
    r.start(single=True, seed_index=0, task_ids=["user"])
    await r.job
    assert len(state["samples"]) == 1 and state["samples"][0]["id"] == sample["id"]
    assert r.status["accepted"] == 1


async def test_http_contract_and_bounded_response():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "unit-test" and payload["stream"] is False
        assert str(request.url) == "http://test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ]
            },
        )

    client = LLMClient(httpx.MockTransport(handler))
    result = await client.complete(
        {
            "id": "test",
            "base_url": "http://test/v1",
            "model": "unit-test",
            "api_key": "secret",
        },
        [{"role": "user", "content": "test"}],
    )
    assert result["content"] == "ok"


async def test_error_body_does_not_leak_secret():
    client = LLMClient(
        httpx.MockTransport(lambda r: httpx.Response(401, text="secret key"))
    )
    with pytest.raises(ValueError, match="HTTP 401") as e:
        await client.complete(
            {"id": "test", "base_url": "http://test", "model": "unit"}, []
        )
    assert "secret key" not in str(e.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["automatic"].update(autosave_every=0),
        lambda c: c["endpoints"][0].update(base_url="file:///etc/passwd"),
        lambda c: c["endpoints"][0].update(timeout=-1),
        lambda c: c["tasks"][0].update(endpoint="missing"),
        lambda c: c["automatic"].update(delay_seconds=0),
        lambda c: c.update(max_tool_calls=1000000),
    ],
)
def test_bad_config(mutate):
    c = default_config()
    mutate(c)
    with pytest.raises((ValueError, TypeError)):
        validate_config(c)


def test_window_order_and_prefix_visibility(compiler, sample):
    sample["replay"]["1"] = [
        {"op": "generate", "window": "a", "content": "xyz"},
        {"op": "append", "window": "b", "source": "user", "content": "instant"},
    ]
    p = compiler.compile(sample)
    w = selected_windows(p, compiler, {"visible": ["b", "a"], "visible_tick": 1})
    assert [v["window"] for v in w] == ["b", "a"]
    assert w[1]["content"] == "x"


async def test_tool_results_remain_live_through_reasoning(compiler):
    c = minimal_config()
    fake = FakeClient(
        [
            "Check the clock.",
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "clock",
                        "type": "function",
                        "function": {"name": "time_now", "arguments": "{}"},
                    }
                ],
            },
            "The simulated timestamp is recorded.",
        ]
    )
    b = SampleBuilder(compiler, c, fake)
    s = await b.generate()
    p = compiler.compile(s)
    reasoning = next(
        e for e in p["events"] if e["window"] == "reasoning" and e["op"] == "generate"
    )
    result = next(w for w in p["windows"] if w["id"].startswith("tool-result-"))
    assert result["open"] < reasoning["start"] <= reasoning["end"] < result["close"]
    assert any(r["op"] == "noop" and r["window"] == result["id"] for r in p["rows"])


async def test_explicit_start_prefix_limits_task_context(compiler, sample):
    sample["replay"]["1"] = [
        {
            "op": "append",
            "window": "a",
            "source": "tool",
            "delivery": "stream",
            "content": "abcdef",
        }
    ]
    sample["replay"]["5"] = [{"op": "generate", "window": "b", "content": "FUTURE"}]
    c = minimal_config()
    fake = FakeClient(["reaction"])
    b = SampleBuilder(compiler, c, fake)
    b.sample = sample
    task = {
        "id": "early",
        "kind": "generate",
        "endpoint": "generator",
        "start": "2",
        "visible": ["a", "b"],
        "layout": "json",
        "prompt": "React",
        "tools": False,
    }
    s = Sandbox()
    try:
        await b.task(task, s)
    finally:
        s.close()
    windows = json.loads(fake.messages[0][1]["content"])["windows"]
    assert next(w for w in windows if w["window"] == "a")["content"] == "ab"
    assert next(w for w in windows if w["window"] == "b")["content"] == ""


async def test_pause_on_failure_and_resume(compiler, tmp_path):
    c = minimal_config()
    c["tasks"] = c["tasks"][:1]
    c["automatic"].update(
        validate=False,
        failure_action="pause",
        regenerations=0,
        max_consecutive_failures=3,
    )
    state = {"samples": [], "config": c, "revision": 0}
    runner = Runner(
        state,
        compiler,
        tmp_path / "checkpoint",
        FakeClient([ValueError("crashed"), "Recovered."]),
    )
    runner.start()
    for _ in range(50):
        if runner.status["paused"]:
            break
        await asyncio.sleep(0.01)
    assert runner.status["paused"] and not state["samples"]
    runner.resume()
    await asyncio.wait_for(runner.job, 2)
    assert runner.status["accepted"] == 1


async def test_multiple_acceptances_flush_below_autosave_threshold(compiler, tmp_path):
    c = minimal_config()
    c["tasks"] = c["tasks"][:1]
    c["automatic"].update(
        validate=False, max_samples=2, autosave_every=5, regenerations=0
    )
    state = {"samples": [], "config": c, "revision": 0}
    r = Runner(
        state, compiler, tmp_path / "checkpoint", FakeClient(["First.", "Second."])
    )
    r.start()
    await r.job
    assert len(json.loads((tmp_path / "checkpoint").read_text())["samples"]) == 2
