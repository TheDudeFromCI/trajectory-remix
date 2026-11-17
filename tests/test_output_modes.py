"""Endpoint output modes, for servers without native JSON-schema output, and the
walkthrough showing exactly the body that is sent."""

import json

import pytest
from fastapi.testclient import TestClient
from test_graph_generation import service

from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import request_body, response_contract
from trajectory_remix.graph.studio_api import create_studio_app

SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


def payload(mode, **endpoint):
    config = Endpoint.model_validate(
        dict(url="http://127.0.0.1:8080/v1", model="local", output_mode=mode, **endpoint)
    ).model_dump(mode="json")
    return dict(
        operation="event",
        schema=SCHEMA,
        endpoint_config=config,
        messages=[dict(role="system", content="rules"), dict(role="user", content="data")],
    )


def test_auto_uses_native_schema_without_putting_it_in_the_prompt():
    response_format, messages = response_contract(payload("auto"))
    assert response_format["type"] == "json_schema"
    assert "Schema:" not in json.dumps(messages)


@pytest.mark.parametrize("mode,expected", [("json_object", "json_object"), ("prompt", None)])
def test_fallback_modes_always_state_the_schema_in_the_prompt(mode, expected):
    response_format, messages = response_contract(payload(mode))
    assert (response_format or {}).get("type") == expected
    assert [m["role"] for m in messages] == ["system", "user"]  # Roles still alternate.
    assert json.dumps(SCHEMA) in messages[1]["content"]
    body, _ = request_body(payload(mode), "t-1")
    assert ("response_format" in body) is (expected is not None)


def test_unknown_output_mode_is_rejected():
    with pytest.raises(ValueError):
        payload("grammar")


def test_walkthrough_shows_the_exact_body_the_transport_sends(tmp_path):
    gen, _ = service(tmp_path, fast=False)
    data = gen.store.settings().settings.model_dump(mode="json")
    data["endpoints"]["g"].update(
        output_mode="prompt",
        sticky_sessions=True,
        extra_body={"reasoning_effort": "low"},
    )
    gen.store.update_settings(data, gen.store.settings().revision)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        started = client.post("/api/v1/walkthrough/start", json={"seed": 3}).json()
    wire = started["next"]["request"]["wire"]
    assert (
        wire["reasoning_effort"] == "low" and wire["session_id"] == started["state"]["source"]["id"]
    )
    assert "response_format" not in wire and "Schema:" in wire["messages"][1]["content"]
    assert wire["url"].endswith("/chat/completions")
