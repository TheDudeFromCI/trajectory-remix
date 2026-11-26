"""timeout_seconds = -1 disables the per-request time limit."""

import asyncio

import httpx
import pytest

from trajectory_remix.graph.failure_reason import endpoint_failure
from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import OpenAITransport


def test_minus_one_is_accepted():
    endpoint = Endpoint(url="http://127.0.0.1:1", model="fixture", timeout_seconds=-1)
    assert endpoint.timeout_seconds == -1


@pytest.mark.asyncio
async def test_disabled_timeout_waits_past_normal_limit(monkeypatch):
    seen = {}

    async def handler(request):
        await asyncio.sleep(0.2)
        body = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        return httpx.Response(200, json=body)

    original = httpx.AsyncClient

    def client(**kwargs):
        seen.update(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    payload = dict(
        operation="event",
        endpoint_config=dict(url="http://127.0.0.1:1", model="fixture", timeout_seconds=-1),
        messages=[],
    )
    result = await OpenAITransport()(
        Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)
    )
    assert result["text"] == "ok"
    assert seen["timeout"] is None


def test_failure_message_omits_limit_when_disabled():
    reason = endpoint_failure(TimeoutError(), "e", {"url": "http://h/v1", "timeout_seconds": -1})
    assert "within" not in reason
