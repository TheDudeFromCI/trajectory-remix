"""The assistant logs one parseable line per tool call; the editor shows it as "Running <tool>"."""

import pytest
from test_assistant import finish_with, flagged, patch, run, service
from test_graph_studio import client  # noqa: F401  (pytest fixture)


@pytest.mark.asyncio
async def test_each_assistant_tool_call_is_logged(tmp_path):
    gen, fake = service(tmp_path)
    record, (comment,) = flagged(gen, "Content is wrong.")
    fake.responses = [patch, lambda t: finish_with((comment, True, "Rewrote x."))]
    job = await run(gen, record)
    lines = [i["text"] for i in gen.jobs.logs(job)["items"] if "Assistant tool:" in i["text"]]
    assert len(lines) == 1 and lines[0].endswith("Assistant tool: patch")


def test_job_summaries_report_how_long_a_job_has_run(client):  # noqa: F811
    job = client.post("/api/v1/imports", json=dict(text="{}")).json()["job_id"]
    row = next(r for r in client.get("/api/v1/job-summaries").json() if r["id"] == job)
    assert row["age"] >= 0 and row["created"] > 0
