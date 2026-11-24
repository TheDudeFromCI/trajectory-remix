"""Per-endpoint reasoning level and automatic reconnect after retry exhaustion."""

import pytest
from test_graph_jobs import queue, run, start

from trajectory_remix.graph import GraphStore
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.models import Endpoint
from trajectory_remix.graph.provider import request_body


def body(**options):
    endpoint = Endpoint(url="http://127.0.0.1:1/v1", model="fixture", **options)
    payload = dict(
        operation="event",
        endpoint_config=endpoint.model_dump(mode="json"),
        messages=[],
    )
    return request_body(payload, "t")[0]


def test_defaults_omit_reasoning_and_reconnect():
    endpoint = Endpoint(url="http://127.0.0.1:1/v1", model="fixture")
    assert endpoint.reasoning is None and endpoint.reconnect_seconds is None
    assert "reasoning_effort" not in body()


@pytest.mark.parametrize("level", ["none", "low", "xhigh", "Max-Thinking", "budget:4096"])
def test_reasoning_reaches_wire(level):
    assert body(reasoning=level)["reasoning_effort"] == level


def test_reasoning_validation():
    with pytest.raises(ValueError):
        Endpoint(url="http://x/v1", model="m", reasoning="")
    with pytest.raises(ValueError):
        Endpoint(url="http://x/v1", model="m", extra_body={"reasoning_effort": "low"})
    with pytest.raises(ValueError):
        Endpoint(url="http://x/v1", model="m", reconnect_seconds=0)


def reconnecting(tmp_path, seconds):
    store = GraphStore.initialize(
        tmp_path / "project.sqlite",
        dict(
            endpoints={
                "a": dict(
                    url="http://same.invalid",
                    model="fake",
                    retries=0,
                    reconnect_seconds=seconds,
                )
            }
        ),
    )
    clock = [1000.0]
    return JobService(store, clock=lambda: clock[0]), clock


def test_reconnect_resumes_after_delay(tmp_path):
    jobs, clock = reconnecting(tmp_path, 30)
    job = run(jobs)
    record = start(jobs, job)
    queue(jobs, job, record)
    ticket = jobs.claim("a")
    assert not jobs.retry(ticket)
    assert jobs.reconnect_due() == []
    assert jobs.claim("a") is None
    clock[0] += 29
    assert jobs.reconnect_due() == []
    clock[0] += 1
    assert jobs.reconnect_due() == ["a"]
    assert jobs.claim("a").id == ticket.id


def test_reconnect_disabled_stays_paused(tmp_path):
    jobs, clock = reconnecting(tmp_path, None)
    job = run(jobs)
    queue(jobs, job, start(jobs, job))
    assert not jobs.retry(jobs.claim("a"))
    clock[0] += 10**6
    assert jobs.reconnect_due() == []
    assert jobs.claim("a") is None
