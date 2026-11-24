"""The assistant logs one parseable line per tool call; the editor shows it as "Running <tool>"."""

import pytest
from test_assistant import edit, flagged, reply, run, service, turn
from test_graph_studio import client  # noqa: F401  (pytest fixture)


@pytest.mark.asyncio
async def test_each_assistant_tool_call_is_logged(tmp_path):
    gen, fake = service(tmp_path)
    record, (comment,) = flagged(gen, "Content is wrong.")
    fake.responses = [turn(edit(), reply(comment, True, "Rewrote x."))]
    job = await run(gen, record)
    lines = [i["text"] for i in gen.jobs.logs(job)["items"] if "Assistant tool:" in i["text"]]
    assert [line.rsplit("Assistant tool: ", 1)[1] for line in lines] == ["update_event", "reply"]


def test_job_summaries_report_how_long_a_job_has_run(client):  # noqa: F811
    job = client.post("/api/v1/imports", json=dict(text="{}")).json()["job_id"]
    row = next(r for r in client.get("/api/v1/job-summaries").json() if r["id"] == job)
    assert row["age"] >= 0 and row["created"] > 0
