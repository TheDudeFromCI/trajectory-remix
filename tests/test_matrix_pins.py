"""Pin any category or label metric when sampling: kept exactly, or refused as incompatible."""

import pytest

from trajectory_remix.graph import matrix_pins
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.matrix_fill import matrix_keys
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore


@pytest.fixture
def sampler(tmp_path):
    return SamplerService(JobService(GraphStore.initialize(tmp_path / "p.sqlite")))


def values(sampler, proposal):
    """Every category and label metric a proposal sets."""
    defs = sampler.editor()["configuration"]["definitions"]
    return {
        key: proposal["metrics"]["sampling" if spec["kind"] == "category" else "multi_labels"][key]
        for key, spec in matrix_keys(defs).items()
    }


def kept(got, pins):
    return all(
        sorted(got[k]) == sorted(v) if isinstance(v, list) else got[k] == v for k, v in pins.items()
    )


@pytest.mark.parametrize("seed", range(12))
def test_every_value_of_a_sample_can_be_pinned_at_once(sampler, seed):
    pins = values(sampler, sampler.propose(seed=seed, persist=False)["proposal"])
    assert len(pins) > 30
    got = matrix_pins.sample(sampler, pins, seed=seed + 1000)["proposal"]
    assert kept(values(sampler, got), pins)


def test_no_pins_is_an_ordinary_sample(sampler):
    plain = sampler.propose(seed=5, persist=False)["proposal"]
    assert matrix_pins.sample(sampler, {}, seed=5)["proposal"] == plain


def test_a_late_pin_is_kept_and_the_rest_stays_consistent(sampler):
    for seed in range(10):
        proposal = matrix_pins.sample(sampler, {"incident": "stale_decision"}, seed)["proposal"]
        sampled = proposal["metrics"]["sampling"]
        assert sampled["incident"] == "stale_decision"
        assert sampled["information_state"] == "stale", "the hard implication still applies"
        assert proposal["scenario"]["project_state"]["incident"] == "stale_decision"


def test_pinned_behavior_rebuilds_the_objective(sampler):
    proposal = matrix_pins.sample(sampler, {"focus.behavior": "strategy_revision"}, 3)["proposal"]
    objective = proposal["learning_objective"]
    assert objective["primary_behavior"] == "strategy_revision"
    assert objective["primary_family"] == "strategic_planning"
    assert proposal["metrics"]["sampling"]["decision.axis"] == objective["decision_axis"]


def test_a_zero_weight_option_can_be_pinned(sampler):
    editor = sampler.editor()
    matrix = editor["configuration"]["matrix"]
    for option in matrix["dimensions"]["stakes"]:
        if option["id"] == "high":
            option["weight"] = 0
    sampler.update(matrix, {}, editor["settings_revision"])
    proposal = matrix_pins.sample(sampler, {"stakes": "high"}, 1)["proposal"]
    assert proposal["metrics"]["sampling"]["stakes"] == "high"


def test_pinned_labels_are_kept(sampler):
    pins = {
        "event_tags": [],
        "secondary_competencies": ["strategic_planning"],
        "focus.family": "agent_orchestration",
    }
    proposal = matrix_pins.sample(sampler, pins, 2)["proposal"]
    labels = proposal["metrics"]["multi_labels"]
    assert labels["event_tags"] == [] and labels["secondary_competencies"] == ["strategic_planning"]
    (secondary,) = proposal["learning_objective"]["secondary"]
    assert secondary["family"] == "strategic_planning"
    assert labels["secondary_behaviors"] == [secondary["behavior"]]


@pytest.mark.parametrize(
    ("pins", "message"),
    [
        ({"entry_state": "fresh", "lifecycle": "active"}, "entry_state fresh requires lifecycle"),
        (
            {"focus.behavior": "strategy_revision", "focus.family": "agent_orchestration"},
            "belongs to strategic_planning, not agent_orchestration",
        ),
        (
            {"incident": "stale_decision", "information_state": "conflicting"},
            "incident stale_decision forces information_state stale",
        ),
        (
            {
                "focus.family": "strategic_planning",
                "secondary_competencies": ["strategic_planning"],
            },
            "cannot also be a secondary competency",
        ),
    ],
)
def test_contradicting_pins_are_refused_with_the_reason(sampler, pins, message):
    with pytest.raises(matrix_pins.IncompatiblePins, match=message):
        matrix_pins.sample(sampler, pins, 1)


def test_provenance_records_the_pins_and_the_project_matrix(sampler):
    plain = sampler.propose(seed=1, persist=False)["proposal"]["sampling_provenance"]
    provenance = matrix_pins.sample(sampler, {"stakes": "high"}, 1)["proposal"][
        "sampling_provenance"
    ]
    assert provenance["pinned"] == {"stakes": "high"}
    assert provenance["matrix_sha256"] == plain["matrix_sha256"]
    assert provenance["restricted_matrix_sha256"] != plain["matrix_sha256"]
