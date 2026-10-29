from trajectory_remix.storage import DatasetStore, DatasetState
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


def make_state(path, config, compiler, samples=()):
    store = DatasetStore.initialize(path, compiler, config)
    if samples:
        store.replace(samples, 0)
    return DatasetState(store, config)


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
        lambda c: c.update(automatic={"autosave_every": 0}),
        lambda c: c["endpoints"][0].update(base_url="file:///etc/passwd"),
        lambda c: c["endpoints"][0].update(timeout=-1),
        lambda c: c.update(validation={"endpoint": ""}),
        lambda c: c.update(automatic={"delay_seconds": 0}),
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
    assert w[1]["content"] == "<|cxt|>Window: 0\nPurpose: document<|/cxt|>x"
