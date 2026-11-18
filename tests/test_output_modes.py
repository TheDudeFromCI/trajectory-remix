"""Endpoint output modes, for servers without native JSON-schema output, and the
walkthrough showing exactly the body that is sent."""

import json

import pytest

from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import request_body, response_contract

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
