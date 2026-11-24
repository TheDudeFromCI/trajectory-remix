"""Job log lines carry a status level, and failures explain why without leaking content."""

import asyncio
import sqlite3

import httpx
import pytest
from test_graph_jobs import queue, run, runtime, start  # noqa: F401  (runtime is a fixture)

from trajectory_remix.graph import log_levels
from trajectory_remix.graph.failure_reason import endpoint_failure, local_failure


def lines(jobs, job):
    return [(r["level"], r["text"]) for r in jobs.logs(job, limit=1000)["items"]]


def status_error(status, body=None, headers=None):
    request = httpx.Request("POST", "https://api.example.test/v1/chat/completions")
    response = httpx.Response(status, json=body, headers=headers, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_levels_are_stored_defaulted_and_validated(runtime):  # noqa: F811
    _, jobs = runtime
    job = run(jobs)
    jobs.append_log(job, "plain")
    jobs.append_log(job, "bad", level="error")
    jobs.progress(job, "careful", "warning")
    assert [level for level, _ in lines(jobs, job)] == ["info", "error", "warning"]
    with pytest.raises(ValueError):
        jobs.append_log(job, "nope", level="loud")
    assert set(log_levels.LEVELS) == {"info", "success", "warning", "error"}


def test_legacy_log_table_gains_a_level_column(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute(
        "CREATE TABLE graph_job_logs (sequence INTEGER PRIMARY KEY, text TEXT, bytes INTEGER)"
    )
    db.execute("INSERT INTO graph_job_logs(text,bytes) VALUES('old',3)")
    log_levels.migrate(db)
    log_levels.migrate(db)  # idempotent
    assert db.execute("SELECT level FROM graph_job_logs").fetchone() == ("info",)


def test_http_failures_name_the_status_and_provider_code_only():
    reason = endpoint_failure(
        status_error(429, {"error": {"code": "rate_limit_exceeded", "message": "SECRET BODY"}}),
        "a",
        {"url": "https://api.example.test/v1"},
    )
    assert "HTTP 429" in reason and "rate limited" in reason and "rate_limit_exceeded" in reason
    assert "SECRET BODY" not in reason
    # Free text in the code field is never copied.
    odd = endpoint_failure(status_error(400, {"error": {"code": "has spaces & secrets"}}), "a")
    assert "secrets" not in odd and "provider code" not in odd


def test_http_failure_accepts_nested_parameter_paths():
    reason = endpoint_failure(
        status_error(422, {"error": {"type": "invalid_value", "param": "messages[0].role"}}),
        "a",
    )
    assert "rejected the value for this request field" in reason
    assert "[provider code invalid_value] [parameter messages[0].role]" in reason


@pytest.mark.parametrize("param", ["field=SECRET", "field\nSECRET", "x" * 121, 123, None])
def test_http_failure_omits_invalid_provider_metadata(param):
    reason = endpoint_failure(
        status_error(
            400,
            {"error": {"code": "unknown_parameter", "param": param}},
            {"x-request-id": "SECRET value"},
        ),
        "a",
    )
    assert "check the model name, output mode and extra request fields" in reason
    assert "[provider code unknown_parameter]" in reason
    assert "[parameter " not in reason
    assert "provider request ID" not in reason and "SECRET" not in reason


def test_missing_key_is_named_on_401(monkeypatch):
    monkeypatch.delenv("REMIX_API_KEY_MY_MODEL", raising=False)
    assert "REMIX_API_KEY_MY_MODEL" in endpoint_failure(status_error(401), "my-model")
    monkeypatch.setenv("REMIX_API_KEY_MY_MODEL", "present")
    assert "REMIX_API_KEY_MY_MODEL" not in endpoint_failure(status_error(401), "my-model")


def test_transport_failures_explain_themselves():
    config = {"url": "https://user:pw@api.example.test:8443/v1", "timeout_seconds": 30}
    timeout = endpoint_failure(httpx.ReadTimeout("x"), "a", config)
    assert "api.example.test:8443" in timeout and "30s" in timeout and "pw" not in timeout
    refused = httpx.ConnectError("x")
    refused.__cause__ = ConnectionRefusedError(111, "Connection refused")
    assert "Connection refused" in endpoint_failure(refused, "a", config)
    assert "not valid" in endpoint_failure(httpx.UnsupportedProtocol("x"), "a", config)
    assert "not valid JSON" in endpoint_failure(ValueError("secret"), "a", config)
    assert "secret" not in endpoint_failure(ValueError("secret"), "a", config)


def test_local_failure_names_type_and_place_not_message():
    try:
        raise RuntimeError("model said hunter2")
    except RuntimeError as error:
        text = local_failure(error)
    assert "RuntimeError" in text and "hunter2" not in text
    assert "unknown location" in local_failure(RuntimeError("never raised"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "details", "expected"),
    [
        (503, {"code": "overloaded"}, "the endpoint is overloaded or unavailable"),
        (
            400,
            {"code": "unknown_parameter", "param": "reasoning_effort"},
            "the endpoint does not recognize this request field",
        ),
    ],
)
async def test_request_failure_log_is_levelled_and_explains_why(
    runtime,  # noqa: F811
    status,
    details,
    expected,
):
    store, jobs = runtime
    job = run(jobs)
    queue(jobs, job, start(jobs, job))

    async def failing(ticket):
        raise status_error(
            status,
            {"error": {**details, "message": "SECRET BODY"}},
            {"x-request-id": "req_example_123"},
        )

    await jobs.execute_one("a", failing)  # endpoint a retries once, then pauses
    failed = [(level, text) for level, text in lines(jobs, job) if "Request failed" in text]
    assert [level for level, _ in failed] == ["warning", "error"]
    assert "retrying in the same queue slot" in failed[0][1]
    assert "endpoint paused after retry exhaustion" in failed[1][1]
    for _, text in failed:
        assert f"HTTP {status}" in text and expected in text
        assert f"[provider code {details['code']}]" in text
        if "param" in details:
            assert f"[parameter {details['param']}]" in text
        assert "[provider request ID req_example_123]" in text
        assert "SECRET BODY" not in text


@pytest.mark.asyncio
async def test_slow_requests_log_no_heartbeat_by_default(runtime):  # noqa: F811
    _, jobs = runtime
    job = run(jobs)
    queue(jobs, job, start(jobs, job))
    assert jobs.heartbeat_interval is None

    async def slow(ticket):
        await asyncio.sleep(0.05)
        return {"text": "ok"}

    await jobs.execute_one("a", slow)
    text = " ".join(t for _, t in lines(jobs, job))
    assert "Request started" in text and "Response received" in text
    assert "Still waiting" not in text
