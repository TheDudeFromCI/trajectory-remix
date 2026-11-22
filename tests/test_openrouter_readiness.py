"""Endpoint options for hosted routers such as OpenRouter, and per-job usage and cost.

All HTTP is mocked: these check the request we send and how responses are recorded.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_graph_generation import DRAFT, Fake, finish, native_assembly, service

from trajectory_remix.graph import usage
from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import OpenAITransport, context_budget
from trajectory_remix.graph.studio_api import create_studio_app

OPENROUTER_USAGE = {
    "prompt_tokens": 1200,
    "completion_tokens": 80,
    "total_tokens": 1280,
    "prompt_tokens_details": {"cached_tokens": 900},
    "completion_tokens_details": {"reasoning_tokens": 0},
    "cost": 0.00012,
}
SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


def mock_http(monkeypatch, reply):
    captured = []

    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return captured


def ticket(endpoint, content="x"):
    payload = dict(
        operation="event",
        endpoint_config=Endpoint.model_validate(endpoint).model_dump(mode="json"),
        messages=[dict(role="user", content=content)],
        schema=SCHEMA,
    )
    return Ticket("id", "job", "trajectory-7", "openrouter", "step", 1, "lease", payload)


def reply(content='{"content":"ok"}', finish_reason="stop"):
    return {
        "model": "vendor/model",
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
        "usage": OPENROUTER_USAGE,
    }


BASE = dict(url="https://openrouter.ai/api/v1", model="vendor/model")


def test_bytes_per_token_lets_a_real_window_be_used():
    payload = dict(
        operation="event",
        messages=[dict(role="user", content="x" * 20000)],
        schema=SCHEMA,
        endpoint_config=dict(
            context_window_tokens=16384, max_output_tokens=2048, bytes_per_token=1.0
        ),
    )
    strict = context_budget(payload, {"type": "json_object"}, payload["messages"])
    assert strict["input_tokens_estimate"] > strict["input_ceiling"]  # 1 byte = 1 token.
    payload["endpoint_config"]["bytes_per_token"] = 3.0
    relaxed = context_budget(payload, {"type": "json_object"}, payload["messages"])
    assert relaxed["input_tokens_estimate"] < relaxed["input_ceiling"]
    assert relaxed["input_bytes"] == strict["input_bytes"]
    with pytest.raises(ValidationError):
        Endpoint.model_validate(dict(BASE, bytes_per_token=0.5))  # Would undercount less.


@pytest.mark.asyncio
async def test_extra_body_and_sticky_session_reach_the_request(monkeypatch):
    sent = mock_http(monkeypatch, reply())
    endpoint = dict(
        BASE,
        sticky_sessions=True,
        extra_body={"reasoning": {"effort": "none"}, "provider": {"require_parameters": True}},
    )
    result = await OpenAITransport()(ticket(endpoint))
    (body,) = sent
    assert body["reasoning"] == {"effort": "none"}
    assert body["provider"] == {"require_parameters": True}
    assert body["session_id"] == "trajectory-7"
    assert body["model"] == "vendor/model" and body["response_format"]["type"] == "json_schema"
    assert result["usage"]["cost"] == 0.00012
    await OpenAITransport()(ticket(BASE))
    assert "session_id" not in sent[1] and "reasoning" not in sent[1]  # Off by default.


@pytest.mark.parametrize("field", ["model", "messages", "response_format", "max_tokens"])
def test_extra_body_cannot_replace_controlled_fields(field):
    with pytest.raises(ValidationError, match="extra_body cannot set"):
        Endpoint.model_validate(dict(BASE, extra_body={field: "x"}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,finish,phrase",
    [('{"content":"half', "length", "cut off"), ("not json", "stop", "not a single valid JSON")],
)
async def test_unusable_replies_keep_usage_and_explain_themselves(
    monkeypatch, content, finish, phrase
):
    from trajectory_remix.graph.provider import invalid_reason

    mock_http(monkeypatch, reply(content, finish))
    result = await OpenAITransport()(ticket(BASE))
    assert result["invalid_output"] is True and "output" not in result
    assert result["usage"] == OPENROUTER_USAGE  # Billed tokens are still recorded.
    assert phrase in invalid_reason(result)


def test_usage_counts_read_openrouter_fields_and_keep_unknowns_missing():
    values = usage.counts(OPENROUTER_USAGE)
    assert values == dict(
        prompt_tokens=1200,
        completion_tokens=80,
        cached_tokens=900,
        reasoning_tokens=0,
        cost=0.00012,
    )
    bare = usage.counts({"prompt_tokens": 5})
    assert bare["cost"] is None and bare["cached_tokens"] is None  # Never zero.
    assert "cost=0.000120" in usage.describe(values)


class Billed:
    """The fixture Draft and Assembly replies, with OpenRouter-style usage on every reply."""

    def __init__(self, cost=True):
        self.inner, self.cost = Fake(), cost

    async def __call__(self, ticket):
        result = dict(await self.inner(ticket), model="scripted-offline")
        billed = dict(OPENROUTER_USAGE)
        if not self.cost:
            billed.pop("cost")
        return dict(result, usage=billed)


@pytest.mark.asyncio
async def test_job_usage_totals_and_per_trajectory_average(tmp_path):
    fake = Billed()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    report = usage.summary(gen.store, job)
    calls = len(fake.inner.calls)
    assert report["trajectories"] == 1 and report["models"] == ["scripted-offline"]
    assert report["total"]["requests"] == calls
    assert report["total"]["prompt_tokens"] == 1200 * calls
    assert report["total"]["cached_tokens"] == 900 * calls
    assert report["total"]["cost"] == pytest.approx(0.00012 * calls)
    assert report["per_trajectory"]["cost"] == pytest.approx(0.00012 * calls)
    assert report["wall_seconds"] is not None and report["wall_seconds"] >= 0
    assert report["per_trajectory"]["seconds"] == report["total"]["seconds"]  # One trajectory.
    assert set(report["operations"]) == {"draft", "assembly"}
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        served = client.get(f"/api/v1/jobs/{job}/usage").json()
    assert served["total"]["requests"] == calls
    logs = "\n".join(e["text"] for e in gen.jobs.logs(job, limit=1000)["items"])
    assert "cached_tokens=900" in logs and "cost=0.000120" in logs


@pytest.mark.asyncio
async def test_cost_total_is_missing_when_any_response_lacked_it(tmp_path):
    gen, _ = service(tmp_path, Billed(cost=False), fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    report = usage.summary(gen.store, job)
    assert report["total"]["cost"] is None and report["total"]["cost_reported"] == 0
    assert report["total"]["prompt_tokens"] > 0


def test_openrouter_example_settings_are_valid():
    from pathlib import Path

    from trajectory_remix.graph.models import ProjectSettings

    path = Path(__file__).parents[1] / "examples" / "openrouter.json"
    settings = ProjectSettings.model_validate(json.loads(path.read_text()))
    endpoint = settings.endpoints[settings.endpoint_roles["draft"]]
    assert endpoint.sticky_sessions and endpoint.bytes_per_token == 3.0
    assert endpoint.extra_body["provider"] == {"require_parameters": True}
    assert not settings.generation.fast and not settings.generation.critical


@pytest.mark.asyncio
async def test_full_job_through_the_real_transport_with_the_example_settings(tmp_path, monkeypatch):
    """End to end over HTTP: example OpenRouter settings, real transport, mocked server."""
    from pathlib import Path

    from trajectory_remix.graph.bulk import BulkService
    from trajectory_remix.graph.generation import GenerationService
    from trajectory_remix.graph.jobs import JobService
    from trajectory_remix.graph.sampling import SamplerService
    from trajectory_remix.graph.storage import GraphStore

    example = Path(__file__).parents[1] / "examples" / "openrouter.json"
    store = GraphStore.initialize(tmp_path / "p.sqlite", json.loads(example.read_text()))
    monkeypatch.setenv("REMIX_API_KEY_OPENROUTER", "test-key")
    requests = []

    async def openrouter(request):
        body = json.loads(request.content)
        requests.append((request, body))
        system, user = body["messages"]  # Roles alternate: one system, one user message.
        assert system["role"] == "system" and user["role"] == "user"
        text = user["content"]
        if "tools" in body:
            assert "Trajectory Id: `" in text and "Draft:\n```" in text
        else:
            assert "# Draft a synthetic episode" in text and "Sampled proposal:" in text
        if "tools" in body:
            message = native_assembly(body, body["session_id"])
            finish_reason = "tool_calls"
        else:
            message = {"content": json.dumps({"draft": DRAFT})}
            finish_reason = "stop"
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"finish_reason": finish_reason, "message": message}],
                "usage": OPENROUTER_USAGE,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(openrouter), **kwargs),
    )
    jobs = JobService(store, active_limit=2)
    sampler = SamplerService(jobs)
    gen = GenerationService(jobs, sampler, BulkService(jobs), OpenAITransport())
    job = gen.start([{"seed": 11}], {})
    await finish(gen, job)

    assert gen.jobs.inspect(job)["state"] == "completed"
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    source = gen.store.read(ident).source
    assert source.metadata["stage"] == 0 and "ack:echo" in source.events
    for request, body in requests:
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "mistralai/mistral-small-3.2-24b-instruct"
        assert body["session_id"] == ident and body["provider"] == {"require_parameters": True}
        assert body["max_tokens"] == 8192 and body["stream"] is False
        assert "test-key" not in json.dumps(body)  # The key travels only in the header.
    report = usage.summary(gen.store, job)
    assert report["total"]["requests"] == len(requests)
    assert report["total"]["cost"] == pytest.approx(0.00012 * len(requests))
    assert report["models"] == ["mistralai/mistral-small-3.2-24b-instruct"]
    logs = "\n".join(e["text"] for e in gen.jobs.logs(job, limit=1000)["items"])
    assert "test-key" not in logs and "Visible observation" not in logs  # No secrets or bodies.
