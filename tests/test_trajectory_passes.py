"""The Editor's Draft tab: New draft from the matrix, Assemble in place, and Sample unset."""

from uuid import uuid4

import pytest
from test_graph_generation import DRAFT, finish, native_report, service, tool_message

from trajectory_remix.graph import matrix_fill, trajectory_passes
from trajectory_remix.graph.metrics import MetricService
from trajectory_remix.graph.storage import RevisionConflict


def metric(value):
    return dict(value=value, origin="manual_annotation")


def trajectory(gen, *, metrics=None, draft=None, stage=-3):
    metadata = dict(stage=stage, metrics=metrics or {})
    if draft is not None:
        metadata["draft"] = draft
    return gen.store.create(
        dict(
            format="kidra-trajectory-graph/1",
            id=str(uuid4()),
            windows={"reason": {"purpose": "reasoning", "agent_writable": True}},
            events={"x": dict(kind="agent_text", window="reason", content="before")},
            metadata=metadata,
        )
    )


def definitions(gen):
    return gen.sampler.editor()["configuration"]["definitions"]


# domain and complexity go to the supplied sampler; stakes is pinned through the matrix.
ASSIGNED = dict(domain="scientific_research", stakes="high", complexity="hard")


@pytest.mark.asyncio
async def test_new_draft_keeps_assigned_metrics_and_assigns_the_rest(tmp_path):
    gen, fake = service(tmp_path)
    record = trajectory(gen, metrics={k: metric(v) for k, v in ASSIGNED.items()})
    job, delta, filled = trajectory_passes.start_draft(gen, record.source.id, record.revision, 7)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.metadata["draft"] == DRAFT
    assert source.events["x"].content == "before" and source.metadata["stage"] == -3
    assert gen.jobs.inspect(job)["state"] == "completed"
    (call,) = fake.calls
    assert call.payload["operation"] == "draft" and call.endpoint == "g"
    sampled = source.metadata["proposal"]["proposal"]["metrics"]["sampling"]
    saved = source.metadata["metrics"]
    for key, value in ASSIGNED.items():
        assert sampled[key] == value and saved[key]["origin"] == "manual_annotation"
        assert f'"{key}":"{value}"' in call.payload["messages"][1]["content"]
    assert set(filled).isdisjoint(ASSIGNED) and "lifecycle" in filled
    for key in filled:
        assert saved[key]["origin"] == matrix_fill.ORIGIN
        assert saved[key]["value"] == matrix_fill._sampled(
            source.metadata["proposal"]["proposal"], key, definitions(gen)[key]
        )
    assert "suggested_task_count" in filled, "numeric targets are assigned too"
    assert delta["before"]["metadata"]["metrics"].keys() == set(ASSIGNED)
    assert source.metadata["targets"] == source.metadata["proposal"]["proposal"]["metrics"]


def test_an_unknown_choice_is_not_a_pin(tmp_path):
    gen, _ = service(tmp_path)
    record = trajectory(gen, metrics={"decision.choice": metric("x"), "stakes": metric("high")})
    assert matrix_fill.assigned(record.source, definitions(gen)) == {"stakes": "high"}


def test_incompatible_assigned_metrics_refuse_a_new_draft(tmp_path):
    gen, _ = service(tmp_path)
    pins = {"entry_state": metric("fresh"), "lifecycle": metric("active")}
    record = trajectory(gen, metrics=pins)
    with pytest.raises(ValueError, match="entry_state fresh requires lifecycle"):
        trajectory_passes.start_draft(gen, record.source.id, record.revision, 1)
    assert gen.store.read(record.source.id).revision == record.revision, "nothing is saved"


@pytest.mark.asyncio
async def test_assemble_edits_the_existing_graph_in_place(tmp_path):
    gen, fake = service(tmp_path)
    record = trajectory(gen, draft="Agent says after, then adds y.")
    fake.responses = [
        lambda t: tool_message(
            ("update_event", {"event_id": "x", "content": "after", "close_window": False}),
            (
                "create_event",
                dict(
                    event_id="y",
                    kind="agent_text",
                    window="reason",
                    content="more",
                    after=["x"],
                    close_window=True,
                ),
            ),
            native_report(t.payload, ["x", "y"]),
        )
    ]
    job = trajectory_passes.start_assemble(gen, record.source.id, record.revision)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    (call,) = fake.calls
    assert call.payload["operation"] == "assembly"
    assert "already has 1 events in 1 windows" in call.payload["messages"][1]["content"]
    assert "Agent says after" in call.payload["messages"][1]["content"]
    assert source.events["x"].content == "after" and set(source.events) == {"x", "y"}
    assert source.metadata["draft"] == "Agent says after, then adds y."
    assert source.metadata["stage"] == 0, "Unfinished becomes Pending once assembled"
    assert gen.jobs.inspect(job)["state"] == "completed"


@pytest.mark.asyncio
async def test_a_failed_pass_keeps_the_stage_and_graph(tmp_path):
    gen, fake = service(tmp_path, format_retries=0)
    record = trajectory(gen, stage=1)
    fake.responses = [{"draft": "not text"}]
    job, _, _ = trajectory_passes.start_draft(gen, record.source.id, record.revision, 3)
    await finish(gen, job)
    source = gen.store.read(record.source.id).source
    assert source.metadata["stage"] == 1 and "draft" not in source.metadata
    assert source.events["x"].content == "before"


def test_assemble_needs_a_draft_and_draft_needs_the_role(tmp_path):
    gen, _ = service(tmp_path)
    record = trajectory(gen)
    with pytest.raises(ValueError, match="draft before assembling"):
        trajectory_passes.start_assemble(gen, record.source.id, record.revision)
    with pytest.raises(RevisionConflict):
        trajectory_passes.start_draft(gen, record.source.id, record.revision + 1)
    current = gen.store.settings()
    current.settings.endpoint_roles = {"fast": "f"}
    gen.store.update_settings(current.settings, current.revision)
    with pytest.raises(ValueError, match="Draft endpoint"):
        trajectory_passes.start_draft(gen, record.source.id, record.revision)


def test_sample_unset_fills_only_unset_metrics_with_an_undo_step(tmp_path):
    gen, _ = service(tmp_path)
    record = trajectory(gen, metrics={"domain": metric("scientific_research")}, draft="keep")
    metrics = MetricService(gen.sampler)
    view = trajectory_passes.sample_unset(metrics, record.source.id, record.revision, seed=2)
    source = gen.store.read(record.source.id).source
    assert "domain" not in view["filled"] and "lifecycle" in view["filled"]
    assert "secondary_behaviors" in view["filled"], "an empty label sample is a value too"
    assert view["values"]["lifecycle"]["status"] == "available"
    assert source.metadata["metrics"]["domain"]["origin"] == "manual_annotation"
    assert "proposal" not in source.metadata and source.metadata["draft"] == "keep"
    assert view["delta"]["before"]["metadata"]["metrics"].keys() == {"domain"}
    again = trajectory_passes.sample_unset(metrics, record.source.id, view["revision"], seed=3)
    assert again["filled"] == [] and again["revision"] == view["revision"]


@pytest.mark.asyncio
async def test_new_draft_writes_for_the_target_size_not_the_current_size(tmp_path):
    gen, fake = service(tmp_path)
    record = trajectory(gen, metrics={"task_length": metric("XL")})  # One short event: tiny.
    job, _, filled = trajectory_passes.start_draft(gen, record.source.id, record.revision, 4)
    await finish(gen, job)
    proposal = gen.store.read(record.source.id).source.metadata["proposal"]["proposal"]
    assert "task_length" not in filled
    assert proposal["scenario"]["project_state"]["task_length"] == "XL"
    assert (
        "Size the trajectory for task length XL" in fake.calls[0].payload["messages"][1]["content"]
    )
