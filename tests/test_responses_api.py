"""The opt-in Responses transport uses the shared job and tool execution paths."""

import json

import httpx
import pytest
from pydantic import ValidationError
from test_assistant import flagged, run, service

from trajectory_remix.graph import usage
from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import OpenAITransport, request_body

BASE = {"url": "https://api.openai.example/v1/", "model": "test-model"}
USAGE = {
    "input_tokens": 120,
    "output_tokens": 35,
    "input_tokens_details": {"cached_tokens": 32},
    "output_tokens_details": {"reasoning_tokens": 20},
}


def mock_http(monkeypatch, *replies):
    requests = []
    pending = iter(replies)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=next(pending))

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


def draft(**config):
    return Ticket(
        "id",
        "job",
        "trajectory",
        "example",
        "step",
        1,
        "lease",
        dict(
            operation="draft",
            endpoint_config=BASE | config,
            messages=[
                dict(role="system", content="Guide"),
                dict(role="user", content="Write notes"),
            ],
        ),
    )


def message(text):
    return dict(
        id="msg_1",
        type="message",
        role="assistant",
        status="completed",
        content=[dict(type="output_text", text=text, annotations=[])],
    )


def function_call(call_id, name, arguments):
    return dict(
        id="fc_" + call_id,
        type="function_call",
        status="completed",
        call_id=call_id,
        name=name,
        arguments=json.dumps(arguments),
    )


@pytest.mark.asyncio
async def test_draft_routes_and_sends_responses_options_only_when_enabled(monkeypatch):
    chat = {"choices": [{"finish_reason": "stop", "message": {"content": "Chat notes"}}]}
    responses = dict(
        status="completed", output=[message("Responses notes")], usage=USAGE, model="test-model"
    )
    sent = mock_http(monkeypatch, chat, responses)
    monkeypatch.setenv("REMIX_API_KEY_EXAMPLE", "test-key")
    transport = OpenAITransport()
    assert not Endpoint.model_validate(BASE).use_responses_api
    assert (await transport(draft(reasoning="low", max_output_tokens=300)))["text"] == "Chat notes"
    ticket = draft(
        use_responses_api=True,
        reasoning="low",
        max_output_tokens=300,
        sticky_sessions=True,
        presence_penalty=0.5,
        extra_body={"prompt_cache_key": "demo"},
    )
    result = await transport(ticket)
    assert result["text"] == "Responses notes"
    assert usage.counts(result["usage"]) == dict(
        prompt_tokens=120, completion_tokens=35, cached_tokens=32, reasoning_tokens=20, cost=None
    )
    first, second = sent
    assert first.url.path == "/v1/chat/completions" and second.url.path == "/v1/responses"
    assert second.headers["Authorization"] == "Bearer test-key"
    first_body = json.loads(first.content)
    assert first_body["reasoning_effort"] == "low" and first_body["max_tokens"] == 300
    assert json.loads(second.content) == dict(
        input=ticket.payload["messages"],
        model="test-model",
        stream=False,
        store=False,
        reasoning={"effort": "low"},
        max_output_tokens=300,
        prompt_cache_key="demo",
    )


@pytest.mark.asyncio
async def test_assistant_replays_reasoning_and_tool_results_with_responses(monkeypatch, tmp_path):
    gen, _ = service(tmp_path)
    record, (comment_id,) = flagged(gen, "Change x.")
    current = gen.store.settings()
    config = current.settings.model_dump(mode="json")
    config["endpoints"]["a"].update(use_responses_api=True, reasoning="low")
    gen.store.update_settings(config, current.revision)
    reasoning = dict(id="rs_1", type="reasoning", summary=[], encrypted_content="opaque-reasoning")
    edit = function_call("call_edit", "update_event", {"event_id": "x", "content": "updated"})
    reply = function_call(
        "call_reply", "reply", {"comment_id": comment_id, "done": True, "note": "Updated x."}
    )
    sent = mock_http(
        monkeypatch,
        dict(status="completed", output=[reasoning, message("I'll fix it."), edit], usage=USAGE),
        dict(status="completed", output=[reply], usage=USAGE),
    )
    gen.transport = OpenAITransport()
    job = await run(gen, record)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert gen.store.read(record.source.id).source.events["x"].content == "updated"
    assert gen.comments.for_trajectory(record.source.id)[0]["status"] == "addressed"
    first, second = (json.loads(request.content) for request in sent)
    assert first["reasoning"] == {"effort": "low"}
    assert "max_output_tokens" not in first
    assert first["include"] == ["reasoning.encrypted_content"] and first["store"] is False
    edit_tool = next(tool for tool in first["tools"] if tool["name"] == "update_event")
    assert edit_tool["type"] == "function" and edit_tool["strict"] is False
    assert "parameters" in edit_tool and "function" not in edit_tool
    assert second["input"][:-4] == first["input"]
    assert second["input"][-4:-1] == [reasoning, message("I'll fix it."), edit]
    tool_result = second["input"][-1]
    assert tool_result["type"] == "function_call_output" and tool_result["call_id"] == "call_edit"
    assert json.loads(tool_result["output"])["accepted"] is True


def test_switching_an_endpoint_replays_history_in_the_selected_format():
    call = function_call("call_1", "reply", {})
    previous = dict(
        role="assistant",
        content="Answer.",
        tool_calls=[
            dict(id=call["call_id"], type="function", function=dict(name="reply", arguments="{}"))
        ],
    )
    output = dict(role="tool", tool_call_id=call["call_id"], content="{}")
    payload = draft(use_responses_api=True).payload | {"messages": [previous, output]}
    body, _ = request_body(payload, "trajectory")
    assert body["input"] == [
        dict(role="assistant", content="Answer."),
        dict(type="function_call", call_id=call["call_id"], name="reply", arguments="{}"),
        dict(type="function_call_output", call_id=call["call_id"], output="{}"),
    ]
    payload["messages"][0] = previous | {"responses_output": [message("Answer."), call]}
    payload["endpoint_config"]["use_responses_api"] = False
    body, _ = request_body(payload, "trajectory")
    assert body["messages"] == [previous, output]


@pytest.mark.asyncio
async def test_incomplete_responses_never_return_partial_output(monkeypatch):
    mock_http(
        monkeypatch,
        dict(
            status="incomplete",
            incomplete_details={"reason": "max_output_tokens"},
            output=[message("Partial")],
            usage=USAGE,
        ),
    )
    result = await OpenAITransport()(draft(use_responses_api=True))
    assert result == dict(model=None, usage=USAGE, invalid_output=True, finish_reason="length")


def test_responses_extra_fields_cannot_override_managed_history_or_reasoning():
    extras = {"reasoning": {"effort": "low"}, "previous_response_id": "resp_1"}
    assert Endpoint.model_validate(BASE | {"extra_body": extras}).extra_body == extras
    with pytest.raises(
        ValidationError, match="extra_body cannot set previous_response_id, reasoning"
    ):
        Endpoint.model_validate(BASE | {"use_responses_api": True, "extra_body": extras})
