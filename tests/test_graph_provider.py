"""Wire schema choices preserve the local graph contract and endpoint identity."""

import json

import httpx
import pytest

from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.provider import OpenAITransport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,schema,mode",
    [
        (
            "event",
            {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
            "json_schema",
        ),
        (
            "event",
            {
                "type": "object",
                "properties": {"arguments": {}},
                "required": ["arguments"],
                "additionalProperties": False,
            },
            "json_object",
        ),
        (
            "repair",
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "action": {"const": "inspect"},
                            "event_id": {"type": "string"},
                        },
                        "required": ["action", "event_id"],
                        "additionalProperties": False,
                    }
                ]
            },
            "json_object",
        ),
    ],
)
async def test_native_and_arbitrary_output_contracts(monkeypatch, operation, schema, mode):
    captured = []

    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "real-model",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"content":"complete"}'}}
                ],
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    messages = [{"role": "user", "content": "visible actor context"}]
    payload = dict(
        operation=operation,
        endpoint_config={"url": "http://127.0.0.1:1/v1", "model": "same-model"},
        messages=messages,
        schema=schema,
    )
    ticket = Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)
    assert (await OpenAITransport()(ticket))["output"] == {"content": "complete"}
    (body,) = captured
    assert body["model"] == "same-model" and body["stream"] is False
    assert body["response_format"]["type"] == mode
    assert messages == [{"role": "user", "content": "visible actor context"}]
    if mode == "json_object":
        # The fixed schema contract precedes the per-call data for prefix caching.
        assert json.dumps(schema) in body["messages"][0]["content"]
        assert body["messages"][1:] == messages
    else:
        assert body["messages"] == messages
        assert body["response_format"]["json_schema"]["schema"] == schema
        assert body["response_format"]["json_schema"]["strict"] is True
