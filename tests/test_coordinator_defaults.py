"""Coordinator-first defaults: checklists, packaged flow and the derived investment label.

Fake providers only. These tests show structure (keys exist, prompts are delivered, saved
projects are left alone), not that the new prompt wording improves live model output.
"""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_adaptive_flow import episode
from test_graph_generation import finish, service

from trajectory_remix.graph import flow_document
from trajectory_remix.graph.adaptive_flow import load_flow
from trajectory_remix.graph.derived_labels import (
    DerivedLabelCounts,
    strategic_investment,
)
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.metrics import MetricService
from trajectory_remix.graph.models import (
    Criterion,
    GraphSource,
    ProjectSettings,
    default_checklists,
)
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app

PREVIOUS = {
    "core": {
        "objective_alignment",
        "causal_evidence",
        "actor_and_supervision",
        "world_consistency",
        "coherent_reasoning",
        "truthful_episode_outcome",
    },
    "critical": {
        "decision_quality",
        "long_horizon_continuity",
        "exploration_and_recovery",
        "training_value",
    },
}
ADDED = {
    "core": {"coordinator_role", "authorization_respected"},
    "critical": {"delegation_discipline", "strategic_investment_judgment", "execution_progress"},
}


def test_default_checklists_keep_old_keys_and_add_coordination_criteria():
    lists = default_checklists()
    assert set(lists) == {"core", "critical"}
    for tier in lists:
        assert set(lists[tier]) == PREVIOUS[tier] | ADDED[tier]
        for key, item in lists[tier].items():
            assert isinstance(item, Criterion) and item.text.strip(), key
    # An episode with no automation decision cannot honestly pass or fail this item.
    assert lists["critical"]["strategic_investment_judgment"].allow_not_applicable
    assert not lists["core"]["authorization_respected"].allow_not_applicable


def test_new_projects_get_the_new_defaults():
    assert set(ProjectSettings().review_checklists["core"]) >= ADDED["core"]


def test_saved_checklists_are_not_rewritten():
    saved = {"core": {"mine": Criterion(text="A custom rule").model_dump()}, "critical": {}}
    settings = ProjectSettings.model_validate({"review_checklists": saved})
    again = ProjectSettings.model_validate_json(settings.model_dump_json())
    assert set(again.review_checklists["core"]) == {"mine"}
    assert again.review_checklists["critical"] == {}


def test_a_stored_project_keeps_its_checklists_through_reopen_and_unrelated_saves(tmp_path):
    old = {
        tier: {key: Criterion(text=f"old {key}").model_dump() for key in keys}
        for tier, keys in PREVIOUS.items()
    }
    path = tmp_path / "saved.sqlite"
    store = GraphStore.initialize(path, {"review_checklists": old})
    reopened = GraphStore(path).settings()
    assert {t: set(c) for t, c in reopened.settings.review_checklists.items()} == PREVIOUS
    edited = reopened.settings.model_copy(update={"log_cache_mib": 7})
    store.update_settings(edited, reopened.revision)
    kept = GraphStore(path).settings().settings.review_checklists
    assert {t: set(c) for t, c in kept.items()} == PREVIOUS
    assert kept["core"]["causal_evidence"].text == "old causal_evidence"


def test_a_new_project_stores_the_full_defaults(tmp_path):
    stored = GraphStore.initialize(tmp_path / "fresh.sqlite").settings().settings
    assert stored.review_checklists == default_checklists()


def test_packaged_flow_still_validates():
    checked = flow_document.check(text=flow_document.packaged_text())
    assert checked["valid"] and checked["diagnostics"] == []


@pytest.mark.asyncio
async def test_actor_events_receive_the_actor_instruction_verbatim(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False, critical=False)
    await finish(gen, gen.start([{"seed": 137}], {}))
    actor_instruction = load_flow()["flow"]["actor_instruction"]
    kinds = {"agent_text", "tool_call"}
    actor = [
        p
        for p in fake.calls
        if p["operation"] == "event" and p["task"]["envelope"]["kind"] in kinds
    ]
    external = [
        p
        for p in fake.calls
        if p["operation"] == "event" and p["task"]["envelope"]["kind"] not in kinds
    ]
    assert actor and external
    for p in actor:
        assert p["task"]["instruction"].startswith(actor_instruction)
        assert json.loads(p["messages"][1]["content"])["task"].startswith(actor_instruction)
    for p in external:
        assert actor_instruction not in p["task"]["instruction"]


def test_default_checklists_are_served_for_the_dashboard_reset(tmp_path):
    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        served = client.get("/api/v1/settings/default-review-checklists").json()
    expected = {
        tier: {key: item.model_dump(mode="json") for key, item in items.items()}
        for tier, items in default_checklists().items()
    }
    assert served == {"review_checklists": expected}
    # The served shape is exactly what a settings draft stores.
    ProjectSettings.model_validate({"review_checklists": served["review_checklists"]})


def targets(choice="protect_quality", tags=()):
    return {"sampling": {"decision.choice": choice}, "multi_labels": {"event_tags": list(tags)}}


@pytest.mark.parametrize("choice", ["invest_in_automation", "perform_manually", "defer_investment"])
def test_investment_choices_carry_the_derived_label(choice):
    assert strategic_investment(targets(choice))


def test_automation_candidate_tag_carries_the_label_in_any_family():
    assert strategic_investment(targets(tags=["automation_candidate"]))
    assert not strategic_investment(targets(tags=["external_approval"]))
    assert not strategic_investment(targets())
    assert not strategic_investment(None) and not strategic_investment({})


def test_counts_cover_every_primary_family():
    from trajectory_remix.graph.sampling import default_matrix, original

    matrix, counts = default_matrix(), DerivedLabelCounts()
    labelled = set()
    for seed in range(200):
        proposal = original.sample_one(matrix, seed, None, {})
        counts.add(proposal)
        if strategic_investment(proposal["metrics"]):
            labelled.add(proposal["learning_objective"]["primary_family"])
    report = counts.report()["strategic_investment"]
    assert report["of"] == 200 and report["basis"] == "proposal_targets"
    assert 0 < report["proposals"] < 200
    # The tag makes the label cross-cutting rather than one family's own.
    assert len(labelled) > 1


def test_population_report_adds_the_derived_label_without_touching_observations(tmp_path):
    store = GraphStore.initialize(tmp_path / "labels.sqlite")
    sampler = SamplerService(JobService(store))
    metrics = MetricService(sampler)
    for seed in (137, 1, 2, 3):
        snapshot = sampler.propose(seed=seed)
        meta = sampler.metadata(snapshot) | {"stage": 1}
        store.create(
            GraphSource(
                format="kidra-trajectory-graph/1",
                id=str(uuid4()),
                windows={"w": {"purpose": "reasoning", "agent_writable": True}},
                events={"a": {"kind": "agent_text", "window": "w", "content": "a"}},
                metadata=meta,
            )
        )
    report = metrics.population({}, "all")
    label = report["derived_labels"]["strategic_investment"]
    assert label["of"] == report["total"] == 4 and 0 <= label["proposals"] <= 4
    # A derived label is not a metric definition and never becomes a stored observation.
    assert "strategic_investment" not in report["definitions"]
    assert "strategic_investment" not in report["unavailable"]
