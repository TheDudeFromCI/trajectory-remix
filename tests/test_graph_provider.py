"""Requests without tools (the Draft) ask for plain text: no JSON mode or grammar."""

import json

import httpx
import pytest

from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.provider import OpenAITransport


def mock(monkeypatch, content):
    captured = []

    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "real-model",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return captured


def ticket(messages):
    payload = dict(
        operation="draft",
        endpoint_config={"url": "http://127.0.0.1:1/v1", "model": "same-model"},
        messages=messages,
    )
    return Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)


@pytest.mark.asyncio
async def test_plain_text_request_sends_no_response_format(monkeypatch):
    captured = mock(monkeypatch, "Plain working notes, not JSON.")
    messages = [{"role": "user", "content": "visible actor context"}]
    result = await OpenAITransport()(ticket(messages))
    assert result["text"] == "Plain working notes, not JSON."
    (body,) = captured
    assert body["model"] == "same-model" and body["stream"] is False
    assert "response_format" not in body
    # Messages go out unchanged: no schema contract is added to the prompt.
    assert body["messages"] == messages


@pytest.mark.asyncio
async def test_json_looking_reply_is_returned_as_text(monkeypatch):
    mock(monkeypatch, '{"draft": "notes"}')
    result = await OpenAITransport()(ticket([{"role": "user", "content": "x"}]))
    assert result["text"] == '{"draft": "notes"}' and "output" not in result
