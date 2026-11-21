"""Native authoring across source edits, provider sessions, REST and MCP."""

import json

import pytest
from fastapi.testclient import TestClient
from test_graph_compiler import event, profile, source
from test_graph_generation import (
    DRAFT,
    finish,
    native_assembly,
    native_report,
    service,
    tool_message,
)

from trajectory_remix.graph import authoring, authoring_session, reviews
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.compiler import CompileError, GraphCompiler
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.models import GraphSource, ProjectSettings, ToolDefinition
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.studio_api import create_studio_app
from trajectory_remix.graph.tool_listing import presented_ids
from trajectory_remix.graph.validation import diagnose


def apply(graph, name, **args):
    settings = ProjectSettings(
        tools={
            **ProjectSettings().tools,
            "read": ToolDefinition(
                name="Read",
                input_schema={
                    "description": "Read a document",
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            "deny": ToolDefinition(name="Deny all arguments", input_schema=False),
        }
    )
    return authoring.apply(graph, settings, name, args)[0], settings


def test_window_lifecycle_nested_updates_and_body_free_topology():
    graph, settings = apply(
        source({}),
        "configure_window",
        window_id="think",
        purpose="reasoning",
        agent_writable=True,
        reasoning_effort="Low",
    )
    graph, _ = apply(
        graph,
        "create_event",
        event_id="n",
        window="think",
        kind="agent_text",
        content="PRIVATE_BODY",
        supervision={"weight": 0.7},
    )
    graph, _ = apply(graph, "update_event", event_id="n", supervision={"masked": True})
    assert graph.events["n"].supervision.model_dump() == {"masked": True, "weight": 0.7}
    graph, _ = apply(graph, "configure_window", window_id="think", reasoning_effort=None)
    assert graph.windows["think"].reasoning_effort is None
    _, view = authoring.apply(graph, settings, "view_graph", {})
    assert "PRIVATE_BODY" not in json.dumps(view)
    graph, _ = apply(
        graph,
        "update_event",
        event_id="n",
        kind="external_text",
        window="paper",
        delivery={"mode": "streamed"},
    )
    graph, _ = apply(graph, "update_event", event_id="n", delivery={"chain_immediately": False})
    assert graph.events["n"].delivery.model_dump() == {
        "mode": "streamed",
        "chain_immediately": False,
    }
    assert set(graph.windows) == {"paper"}
    graph, _ = apply(graph, "delete_event", event_id="n")
    assert graph.events == graph.windows == {}


def test_native_schema_replaces_exactly_and_call_sync_is_causal():
    graph, settings = apply(source({}), "post_tool_schema", tool_ids=["deny", "deny"])
    assert json.loads(graph.events["tools"].content)["parameters"] is False
    repeated, _ = apply(graph, "post_tool_schema", tool_ids=["deny"])
    assert repeated.to_dict() == graph.to_dict()
    graph, _ = apply(
        graph,
        "create_event",
        event_id="read",
        window="req",
        kind="tool_call",
        call_id="one",
        tool="read",
        content='{"path":"paper"}',
        close_window=True,
    )
    assert presented_ids(graph.events["tools"].content) == {"deny", "read"}
    assert graph.events["read"].after == ["tools"]
    assert graph.events["tools"].after == [] and graph.events["tools"].supervision.masked
    declarations = [json.loads(line) for line in graph.events["tools"].content.splitlines()]
    assert (
        next(d for d in declarations if d["name"] == "read")["parameters"]
        == settings.tools["read"].input_schema
    )
    graph, _ = apply(
        graph,
        "create_event",
        event_id="again",
        window="req2",
        kind="tool_call",
        call_id="two",
        tool="read",
        content='{"path":"paper"}',
        after=["read"],
        close_window=True,
    )
    assert graph.events["again"].after == ["read"]
    graph, _ = apply(graph, "post_tool_schema", tool_ids=["deny"])
    assert presented_ids(graph.events["tools"].content) == {"deny"}
    warnings = [d for d in diagnose(graph, settings) if d.code == "unknown_tool"]
    assert {d.event_id for d in warnings} == {"read", "again"}
    assert all(d.related_events == ["tools"] and "visible listing" in d.message for d in warnings)
    graph, _ = apply(
        graph,
        "create_event",
        event_id="unknown",
        window="req3",
        kind="tool_call",
        call_id="three",
        tool="never-defined",
        content="{}",
        close_window=True,
    )
    assert presented_ids(graph.events["tools"].content) == {"deny"}
    assert any(
        d.code == "unknown_tool" and "Project Settings" in d.message
        for d in diagnose(graph, settings)
        if d.event_id == "unknown"
    )


def test_post_chat_creates_a_closed_call_and_matching_masked_delivery():
    graph, _ = apply(
        source({}),
        "post_chat_message",
        event_id="ask",
        origin="user",
        speaker="Pat",
        content="How much is left?",
    )
    graph, settings = apply(
        graph,
        "post_chat_message",
        event_id="reply",
        origin="agent",
        speaker="Agent",
        content="Three.",
        after=["ask"],
    )
    call, echo = graph.events["reply:call"], graph.events["reply"]
    assert call.close_window and call.after == ["ask", "tools"] and call.call_id == echo.call_id
    assert graph.windows[call.window].agent_writable
    assert echo.after == ["reply:call"] and echo.supervision.masked
    assert json.loads(call.content) == {"content": echo.content}
    assert not diagnose(graph, settings)


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ("{}", "required property"),
        ('{"path":"a","unknown":1}', "Additional properties"),
        ('{"path":"a"}', None),
    ],
)
def test_schema_warning_uses_actual_arguments_and_causal_presentation(arguments, expected):
    graph, settings = apply(
        source({}),
        "create_event",
        event_id="call",
        window="req",
        kind="tool_call",
        call_id="one",
        tool="read",
        content=arguments,
        close_window=True,
    )
    schema = [d for d in diagnose(graph, settings) if d.code == "tool_input_schema"]
    if expected:
        assert expected in schema[0].message
    else:
        assert not schema
    raw = graph.to_dict()
    raw["metadata"] = {"shown_tools": ["read"]}
    raw["events"]["call"]["after"] = []
    raw["events"]["tools"]["after"] = ["call"]
    diagnostics = diagnose(GraphSource.model_validate(raw), settings)
    assert any(d.code == "missing_tool_listing" and d.event_id == "call" for d in diagnostics)
    assert all(d.code != "unknown_tool" for d in diagnostics)


def test_read_only_write_error_cannot_be_disabled_and_agent_opening_warns():
    windows = {"w": dict(purpose="document", agent_writable=False)}
    opened_by_input = source(
        {
            "open": event("external_text", "w"),
            "model": event("agent_text", "w", after=["open"]),
        },
        windows,
    )
    settings = ProjectSettings(disabled_warnings=["read_only_window_write", "window_not_writable"])
    assert [d.code for d in diagnose(opened_by_input, settings)] == ["read_only_window_write"]
    with pytest.raises(CompileError, match="read_only_window_write"):
        GraphCompiler(profile(), settings=settings).compile(opened_by_input)
    model_opening = source({"model": event("agent_text", "w")}, windows)
    assert [(d.code, d.severity) for d in diagnose(model_opening)] == [
        ("window_not_writable", "warning")
    ]
    GraphCompiler(profile()).compile(model_opening)


@pytest.mark.asyncio
async def test_assembly_rejects_enabled_warning_then_continues_to_explicit_signoff(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    fake.responses = [
        {"draft": DRAFT},
        lambda t: tool_message(
            (
                "create_event",
                dict(event_id="n", kind="agent_text", window="think", content="Observation"),
            ),
            native_report(t.payload, ["n"]),
        ),
        {"role": "assistant", "content": "The graph is ready."},
        lambda t: tool_message(
            ("update_event", {"event_id": "n", "close_window": True}),
            native_report(t.payload, ["n"]),
        ),
    ]
    job = gen.start([{"seed": 135}], {})
    await finish(gen, job)
    trajectory = gen.jobs.attempts(job)[0]
    saved = gen.store.read(trajectory["trajectory_id"])
    assert saved.source.metadata["stage"] == 0 and trajectory["outcome"] == "pass"
    assert saved.source.metadata["authoring_signoffs"][-1]["exchanges"] == 3
    initial, corrective, final = [t.payload["messages"] for t in fake.calls[1:]]
    assert corrective[:2] == initial and final[: len(corrective)] == corrective
    assert "open_window" in corrective[-1]["content"] and corrective[-1]["role"] == "user"
    assert "Are you finished?" in final[-1]["content"]
    # One response per tool call in the original order, before the user correction.
    assert [m["tool_call_id"] for m in corrective if m["role"] == "tool"] == [
        "fixture-0",
        "fixture-1",
    ]


@pytest.mark.asyncio
async def test_restart_replays_saved_partial_graph_and_native_conversation(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    job = gen.start([{"seed": 137}], {})
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    for step in ("draft", "assembly"):
        gen._prepare(job, ident)
        ticket = gen.jobs.claim("g")
        assert ticket.payload["operation"] == step
        result = (
            {"output": {"draft": DRAFT}}
            if step == "draft"
            else {"message": native_assembly(ticket.payload, ident, signoff=False)}
        )
        gen.jobs.request_result(ticket, result)
        gen.consume(ticket)
    before = gen.store.read(ident)
    saved = gen.jobs.checkpoint(job, ident)["authoring_session"]["messages"]
    assert before.source.metadata["stage"] == -3
    assert before.source.events["ack:echo"].content
    jobs = JobService(gen.store)
    restarted = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), fake)
    fake.responses = [lambda t: tool_message(native_report(t.payload, ["note"]))]
    jobs.control(job, "resume")
    await finish(restarted, job)
    assert fake.calls[0].payload["messages"] == saved
    after = gen.store.read(ident)
    assert after.intrinsic_hash == before.intrinsic_hash and after.source.metadata["stage"] == 0


def test_rest_mcp_and_signoff_use_the_same_warning_snapshot(tmp_path):
    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        graph = source(
            {"n": event("agent_text", "think")},
            {"think": {"purpose": "reasoning", "agent_writable": True}},
        )
        record = client.post("/api/v1/trajectories", json=graph.to_dict()).json()

        def mcp(name, **args):
            return client.post(
                "/api/mcp/call", json={"name": name, "arguments": {"id": graph.id, **args}}
            )

        current = mcp("view_graph").json()
        assert current["diagnostics"] == record["diagnostics"] and len(current["diagnostics"]) == 1
        args = dict(
            status="complete",
            summary="All evidence shown",
            items={
                "completeness": {
                    "status": "pass",
                    "event_ids": ["n"],
                    "evidence": "Final reasoning",
                }
            },
        )
        rejected = mcp("signoff", expected_revision=record["revision"], **args).json()
        assert not rejected["accepted"] and rejected["diagnostics"] == current["diagnostics"]
        saved_settings = client.get("/api/v1/settings").json()
        saved_settings["settings"]["disabled_warnings"] = [
            "open_window",
            "retired",
            "dependency_cycle",
        ]
        saved = client.put(
            "/api/v1/settings",
            json=dict(
                settings=saved_settings["settings"], expected_revision=saved_settings["revision"]
            ),
        )
        assert saved.status_code == 200
        active = client.get(f"/api/v1/trajectories/{graph.id}").json()
        historic = client.get(
            f"/api/v1/trajectories/{graph.id}?revision={record['revision']}"
        ).json()
        assert mcp("view_graph").json()["diagnostics"] == active["diagnostics"] == []
        assert historic["diagnostics"] == record["diagnostics"]
        signed = mcp("signoff", expected_revision=record["revision"], **args)
        assert signed.status_code == 200 and signed.json()["outcome"] == "pass"
        assert (
            mcp("delete_event", event_id="n", expected_revision=record["revision"]).status_code
            == 409
        )
        new = saved.json()
        new["settings"]["disabled_warnings"] = []
        client.put(
            "/api/v1/settings",
            json=dict(settings=new["settings"], expected_revision=new["revision"]),
        )
        assert client.get(f"/api/v1/trajectories/{graph.id}").json()["review_status"][
            "reasons"
        ] == ["validation_settings_changed"]


@pytest.mark.asyncio
async def test_attached_diagnostics_and_signoff_keep_frozen_warning_settings(tmp_path):
    from trajectory_remix.graph.studio import Studio

    gen, fake = service(tmp_path, fast=False)
    project = gen.store.settings()
    project.settings.disabled_warnings = ["open_window"]
    frozen = gen.store.update_settings(project.settings, project.revision)
    job = gen.start([{"seed": 137}], {})
    project.settings.disabled_warnings = []
    current = gen.store.update_settings(project.settings, frozen.revision)
    fake.responses = [
        {"draft": DRAFT},
        tool_message(
            (
                "create_event",
                dict(event_id="n", kind="agent_text", window="w", content="Observation"),
            )
        ),
        lambda t: tool_message(native_report(t.payload, ["n"])),
    ]
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    for _ in range(2):
        gen._prepare(job, ident)
        ticket = gen.jobs.claim("g")
        gen.jobs.request_result(ticket, await fake(ticket))
        gen.consume(ticket)
    studio = Studio(gen.jobs, gen.bulk, None, None)
    attached = studio.read(ident)
    assert attached["owner"] == job and attached["diagnostics"] == []
    assert attached["diagnostics_settings_revision"] == frozen.revision
    assert studio.commands.author(ident, "view_graph", {})["diagnostics"] == attached["diagnostics"]
    await finish(gen, job)
    final = studio.read(ident)
    assert final["source"]["metadata"]["stage"] == 0 and final["owner"] is None
    assert final["diagnostics_settings_revision"] == current.revision
    assert [d["code"] for d in final["diagnostics"]] == ["open_window"]
    assert final["review_status"]["reasons"] == ["validation_settings_changed"]


def test_review_draft_visibility_is_opt_in_and_requires_fidelity_when_shown():
    graph = source({"n": event("agent_text", "n")}, metadata={"draft": "THIS_DRAFT_IS_DISTINCT"})
    settings = ProjectSettings()
    first = authoring_session.begin(graph, settings, "review", "fast")
    assert "draft_fidelity" not in first["checks"] and "THIS_DRAFT_IS_DISTINCT" not in json.dumps(
        first["messages"]
    )
    settings.generation.review_include_draft = True
    next_review = authoring_session.begin(graph, settings, "review", "fast")
    assert (
        "draft_fidelity" in next_review["checks"]
        and "THIS_DRAFT_IS_DISTINCT" in next_review["messages"][1]["content"]
    )
    assert set(reviews.checklist(settings, "fast")) < next_review["checks"].keys()
