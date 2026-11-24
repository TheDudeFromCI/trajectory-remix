"""Supported sampling controls round-trip and reach the wire unchanged."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_graph_generation import service

from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import OpenAITransport
from trajectory_remix.graph.studio_api import create_studio_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "controls",
    [
        {},
        {"temperature": None, "top_p": None, "presence_penalty": None},
        {"temperature": 0.0, "top_p": 0.0, "presence_penalty": 0.0},
        {"temperature": 1.25, "top_p": 0.9, "presence_penalty": -0.4},
    ],
)
async def test_wire_controls_and_timeout(monkeypatch, controls):
    seen, clients = [], []

    async def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]},
        )

    original = httpx.AsyncClient

    def client(**kwargs):
        clients.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    endpoint = Endpoint(
        url="http://127.0.0.1:1/v1", model="fixture", timeout_seconds=321.5, **controls
    )
    payload = dict(
        operation="event",
        endpoint_config=endpoint.model_dump(mode="json"),
        messages=[],
    )
    ticket = Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)
    assert (await OpenAITransport()(ticket))["text"] == "ok"
    assert clients[0]["timeout"] == 321.5
    for key in ("temperature", "top_p", "presence_penalty"):
        if controls.get(key) is None:
            assert key not in seen[0]
        else:
            assert seen[0][key] == controls[key]
    assert "timeout_seconds" not in seen[0]
    assert not {"top_k", "min_p", "rep_penalty", "frequency_penalty"} & seen[0].keys()


@pytest.mark.asyncio
async def test_timeout_bounds_entire_attempt(monkeypatch):
    cancelled = asyncio.Event()

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    payload = dict(
        operation="event",
        endpoint_config=dict(url="http://127.0.0.1:1", model="fixture", timeout_seconds=0.02),
        messages=[],
    )
    with pytest.raises(TimeoutError):
        await OpenAITransport()(
            Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)
        )
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "key,value",
    [
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("top_p", -0.1),
        ("top_p", 1.1),
        ("presence_penalty", -2.1),
        ("presence_penalty", 2.1),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", float("inf")),
        ("temperature", float("nan")),
        ("top_k", 10),
        ("min_p", 0.1),
        ("rep_penalty", 1.2),
    ],
)
def test_invalid_or_unsupported_controls_rejected(key, value):
    with pytest.raises(ValueError):
        Endpoint(url="http://127.0.0.1:1", model="fixture", **{key: value})


def test_settings_api_schema_round_trip_and_frozen_runs(tmp_path):
    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path)
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        record = client.get("/api/v1/settings").json()
        endpoint = record["settings"]["endpoints"]["g"]
        assert endpoint["temperature"] is None and endpoint["timeout_seconds"] == 120
        endpoint.update(temperature=0.7, top_p=0.95, presence_penalty=0.2, timeout_seconds=240)
        response = client.put(
            "/api/v1/settings",
            json=dict(expected_revision=record["revision"], settings=record["settings"]),
        )
        assert response.status_code == 200
        assert response.json()["settings"]["endpoints"]["g"] == endpoint
        schema = client.get("/api/v1/schema/settings").json()
        assert "timeout_seconds" in json.dumps(schema)
    job = gen.start([{"seed": 137}], {})
    current = gen.store.settings()
    updated = current.settings.model_dump(mode="json")
    updated["endpoints"]["g"].update(temperature=1.1, timeout_seconds=600)
    gen.store.update_settings(updated, current.revision)
    assert gen.settings_for(job).endpoints["g"].temperature == 0.7
    assert gen.settings_for(job).endpoints["g"].timeout_seconds == 240
