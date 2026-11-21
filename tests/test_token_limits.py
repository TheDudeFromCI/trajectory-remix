"""Optional endpoint token limits: -1 leaves a limit to the provider."""

import pytest
from pydantic import ValidationError

from trajectory_remix.graph import token_limits
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import request_body

BASE = dict(url="http://127.0.0.1:8000/v1", model="m")


def payload(**limits):
    endpoint = Endpoint.model_validate(dict(BASE, **limits)).model_dump(mode="json")
    return dict(
        operation="event",
        messages=[dict(role="user", content="x" * 30000)],
        schema={"type": "object"},
        endpoint_config=endpoint,
    )


def test_disabled_limits_send_no_max_tokens_and_skip_the_local_check():
    body, budget = request_body(payload(), "t1")
    assert "max_tokens" not in body
    assert budget["input_ceiling"] is None and not token_limits.exceeds(budget)


def test_output_limit_is_sent_and_reserved_from_the_context():
    body, budget = request_body(payload(context_window_tokens=32768, max_output_tokens=4096), "t")
    assert body["max_tokens"] == 4096
    assert budget["input_ceiling"] == 32768 - 4096 - 512


def test_context_limit_alone_reserves_only_framing():
    body, budget = request_body(payload(context_window_tokens=4096), "t")
    assert "max_tokens" not in body and budget["input_ceiling"] == 4096 - 512
    assert token_limits.exceeds(budget)  # 30000 bytes / 3 is about 10000 tokens.


def test_output_limit_alone_is_sent_without_a_local_check():
    body, budget = request_body(payload(max_output_tokens=65536), "t")
    assert body["max_tokens"] == 65536 and budget["input_ceiling"] is None


@pytest.mark.parametrize(
    "limits",
    [
        dict(context_window_tokens=0),
        dict(context_window_tokens=-2),
        dict(context_window_tokens=1000),
        dict(max_output_tokens=0),
        dict(context_window_tokens=4096, max_output_tokens=3584),
    ],
)
def test_invalid_limits_are_rejected(limits):
    with pytest.raises(ValidationError):
        Endpoint.model_validate(dict(BASE, **limits))
