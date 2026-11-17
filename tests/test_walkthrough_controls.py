"""Walkthrough controls: stop a waiting call, follow saved settings, see cut-off replies."""

import asyncio
import json

import httpx
import pytest
from test_adaptive_flow import episode
from test_graph_generation import service

from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.provider import OpenAITransport
from trajectory_remix.graph.walkthrough import Walkthrough


def walker(tmp_path, transport):
    gen, _ = service(tmp_path, fast=False)
    return gen, Walkthrough(gen.store, gen.sampler, transport)


@pytest.mark.asyncio
async def test_stop_cancels_a_waiting_call_and_keeps_the_state(tmp_path):
    release = asyncio.Event()
    script = episode()

    async def slow(ticket):
        await release.wait()
        return await script(ticket)

    gen, walk = walker(tmp_path, slow)
    state = walk.start(seed=4)["state"]
    step = asyncio.create_task(walk.step(state, call_id="c1"))
    while "c1" not in walk.running:
        await asyncio.sleep(0)
    assert walk.cancel("c1") == {"cancelled": True}
    result = await step
    assert "Stopped" in result["trace"]["error"] and not result["trace"]["accepted"]
    assert result["state"]["checkpoint"] == state["checkpoint"]  # Not counted as a format error.
    assert walk.cancel("c1") == {"cancelled": False}
    release.set()
    again = await walk.step(state, call_id="c2")  # Retrying works normally afterwards.
    assert again["trace"]["accepted"]


@pytest.mark.asyncio
async def test_latest_settings_apply_new_limits_to_the_next_step(tmp_path):
    gen, walk = walker(tmp_path, episode())
    state = walk.start(seed=4)["state"]
    first = state["settings_revision"]
    data = gen.store.settings().settings.model_dump(mode="json")
    data["endpoints"]["g"]["max_output_tokens"] = 8192
    data["endpoints"]["g"]["context_window_tokens"] = 32768
    gen.store.update_settings(data, gen.store.settings().revision)
    frozen = walk.describe(state)["request"]["wire"]
    assert frozen["max_tokens"] == 2048  # Without the option, a walkthrough stays frozen.
    described = walk.describe(state, latest=True)
    assert described["request"]["wire"]["max_tokens"] == 8192
    assert described["settings_revision"] > first
    result = await walk.step(state, latest_settings=True)
    assert result["trace"]["request"]["wire"]["max_tokens"] == 8192
    assert result["trace"]["settings_revision"] == result["state"]["settings_revision"] > first


def mock_reply(monkeypatch, message, finish_reason="length"):
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [{"finish_reason": finish_reason, "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
async def test_walkthrough_shows_a_cut_off_reply_but_jobs_never_keep_it(tmp_path, monkeypatch):
    thinking = "Let me think about the episode carefully. " * 200
    mock_reply(monkeypatch, {"content": "", "reasoning_content": thinking})
    gen, walk = walker(tmp_path, OpenAITransport())
    result = await walk.step(walk.start(seed=4)["state"])
    response = result["trace"]["response"]
    assert response["finish_reason"] == "length" and "cut off" in result["trace"]["error"]
    assert (
        response["raw_reasoning"].startswith("Let me think")
        and len(response["raw_reasoning"]) <= 4001
    )
    assert "raw_content" not in response  # Empty text is not shown.
    # A job's ticket does not ask for the text, so the transport drops it.
    endpoint = gen.store.settings().settings.endpoints["g"].model_dump(mode="json")
    payload = dict(
        operation="event",
        endpoint_config=endpoint,
        messages=[dict(role="user", content="x")],
        schema={"type": "object"},
    )
    job = await OpenAITransport()(Ticket("id", "job", "t", "g", "s", 1, "lease", payload))
    assert job["invalid_output"] and "raw_reasoning" not in json.dumps(job)
