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
        assert saved[key]["value"] == (
            sampled.get(key)
            or source.metadata["proposal"]["proposal"]["metrics"]["multi_labels"][key]
        )
    assert delta["before"]["metadata"]["metrics"].keys() == set(ASSIGNED)
    assert source.metadata["targets"] == source.metadata["proposal"]["proposal"]["metrics"]


def test_an_assigned_value_the_sampler_cannot_pin_is_written_into_the_proposal(tmp_path):
    gen, _ = service(tmp_path)
    defs = definitions(gen)
    record = trajectory(gen, metrics={"stakes": metric("high"), "decision.choice": metric("x")})
    snapshot = matrix_fill.sample(gen.sampler, record.source, defs, seed=1)
    proposal = snapshot["proposal"]
    assert proposal["metrics"]["sampling"]["stakes"] == "high"
    assert proposal["scenario"]["project_state"]["stakes"] == "high"
    # An unknown choice is not an assignment: the sampled value stands.
    assert "decision.choice" not in matrix_fill.assigned(record.source, defs)


def test_honor_rewrites_scenario_and_adds_a_requirement():
    proposal = dict(
        metrics=dict(sampling={"user_tone": "warm"}, multi_labels={}),
        scenario=dict(user={"tone": "warm"}, project_state={}),
        generation_directive=dict(requirements=[]),
        sampling_provenance={},
    )
    defs = {"user_tone": dict(kind="category")}
    assert matrix_fill.honor(proposal, {"user_tone": "curt"}, defs) == ["user_tone"]
    assert proposal["scenario"]["user"]["tone"] == "curt"
    assert "user_tone = curt" in proposal["generation_directive"]["requirements"][-1]
    assert proposal["sampling_provenance"]["assigned_overrides"] == {"user_tone": "curt"}


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
