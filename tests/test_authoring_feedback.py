"""Diagnostics observe ordered edits and limit advice is scoped to each endpoint."""

import json

import pytest
from test_graph_compiler import source
from test_graph_generation import finish, native_assembly, service, tool_message

from trajectory_remix.graph import authoring_session
from trajectory_remix.graph.models import ProjectSettings


def test_diagnostics_tool_observes_its_position_in_the_batch():
    settings = ProjectSettings()
    graph = source({})
    session = authoring_session.begin(graph, settings, "repair", "fast")
    message = tool_message(
        (
            "create_event",
            {"event_id": "think", "kind": "agent_text", "window": "work", "content": "Consider."},
        ),
        ("view_diagnostics", {}),
        ("update_event", {"event_id": "think", "close_window": True}),
        ("view_diagnostics", {}),
    )
    updated, terminal = authoring_session.step(graph, settings, session, message)
    assert terminal is None and updated.events["think"].close_window
    assert session["exchanges"] == 1
    replies = {
        reply["tool_call_id"]: json.loads(reply["content"])
        for reply in session["messages"]
        if reply["role"] == "tool"
    }
    assert [d["code"] for d in replies["fixture-1"]["diagnostics"]] == ["open_window"]
    assert replies["fixture-3"]["diagnostics"] == []

    review = authoring_session.begin(updated, settings, "review", "fast")
    names = {tool["function"]["name"] for tool in authoring_session.payload(review)["tools"]}
    assert "view_diagnostics" in names and "update_event" not in names


def advice(gen, job):
    return [
        line
        for line in gen.jobs.logs(job)["items"]
        if "Consider increasing Maximum exchanges per pass" in line["text"]
    ]


@pytest.mark.asyncio
async def test_repeated_limits_log_one_warning_for_the_endpoint(tmp_path):
    gen, fake = service(tmp_path, fast=False, max_authoring_exchanges=1)

    async def transport(ticket):
        if ticket.payload["operation"] == "assembly":
            return {"message": {"role": "assistant", "content": "Still working."}}
        return await fake(ticket)

    gen.transport = transport
    job = gen.start([{"seed": 40}, {"seed": 41}, {"seed": 42}], {})
    await finish(gen, job)
    assert [attempt["outcome"] for attempt in gen.jobs.attempts(job)] == ["fail"] * 3
    (warning,) = advice(gen, job)
    assert warning["level"] == "warning"
    assert "Endpoint g reached the 1-exchange limit twice" in warning["text"]


@pytest.mark.asyncio
async def test_single_limits_on_different_endpoints_do_not_trigger_advice(tmp_path):
    gen, fake = service(tmp_path, critical=False, max_authoring_exchanges=1)
    job = gen.start([{"seed": 40}, {"seed": 41}], {})
    first = gen.jobs.attempts(job)[0]["trajectory_id"]

    async def transport(ticket):
        operation = ticket.payload["operation"]
        if operation == "assembly" and ticket.trajectory_id != first:
            return {"message": native_assembly(ticket.payload, ticket.trajectory_id)}
        if operation in {"assembly", "review"}:
            return {"message": {"role": "assistant", "content": "Still working."}}
        return await fake(ticket)

    gen.transport = transport
    await finish(gen, job)
    assert [attempt["outcome"] for attempt in gen.jobs.attempts(job)] == ["fail"] * 2
    assert [
        gen.jobs.checkpoint(job, attempt["trajectory_id"])["exchange_limit_endpoint"]
        for attempt in gen.jobs.attempts(job)
    ] == ["g", "f"]
    assert advice(gen, job) == []
