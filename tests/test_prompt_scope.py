"""Model prompts exclude storage snapshots and repeated evidence bodies."""

import json
import re

import httpx
import pytest
from test_graph_generation import service

from trajectory_remix.graph import draft_assembly
from trajectory_remix.graph.jobs import Ticket
from trajectory_remix.graph.provider import OpenAITransport, context_budget


def initial(gen):
    job = gen.start([{"seed": 137}], {})
    gen._admit(job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    plans = gen.jobs.inspect(job)["config"]["config"]["plans"]
    return source, next(iter(plans.values()))


def test_real_initial_snapshot_excluded_and_proposal_preserved(tmp_path):
    gen, _ = service(tmp_path)
    source, plan = initial(gen)
    assert len(json.dumps(source.metadata)) > 60000
    payload = draft_assembly.payload(dict(errors=0), plan)
    task = payload["messages"][1]["content"]
    block = re.search(r"Sampled proposal:\n```json\n(.*?)\n```", task, re.S).group(1)
    assert json.loads(block) == plan["proposal"]
    assert "sampling_provenance" in plan["proposal"]
    assert source.id not in task and '"metadata"' not in task
    endpoint = gen.settings_for(gen.jobs.listing()[0]["id"]).endpoints["g"]
    # The prior strict defaults: a 65k window, 8k output and 1 byte per token.
    strict = dict(context_window_tokens=65536, max_output_tokens=8192, bytes_per_token=1.0)
    payload["endpoint_config"] = endpoint.model_copy(update=strict).model_dump(mode="json")
    budget = context_budget(payload, payload["messages"])
    assert budget["input_bytes"] < budget["input_ceiling"]
    assert len(json.dumps(source.metadata)) > 60000  # Storage unchanged.


@pytest.mark.asyncio
async def test_oversized_prompt_stops_before_http_without_truncation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized request reached HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    payload = dict(
        operation="event",
        messages=[dict(role="user", content="x" * 17000)],
        endpoint_config=dict(
            url="http://127.0.0.1:1",
            model="local",
            context_window_tokens=16384,
            max_output_tokens=2048,
            bytes_per_token=1.0,
        ),
    )
    ticket = Ticket("id", "job", "source", "endpoint", "step", 1, "lease", payload)
    result = await OpenAITransport()(ticket)
    assert result["context_limit"] and result["local_budget"]["input_bytes"] > 17000
    assert len(payload["messages"][0]["content"]) == 17000


def test_output_budget_must_leave_input_room():
    from trajectory_remix.graph.models import Endpoint

    with pytest.raises(ValueError):
        Endpoint(
            url="http://127.0.0.1:1",
            model="local",
            context_window_tokens=2048,
            max_output_tokens=2048,
        )
