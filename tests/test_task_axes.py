"""Complexity (Trivial..Expert) and task length (XXS..XXL) as separate sampler axes."""

import pytest

from trajectory_remix.graph import task_axes
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.sampling import (
    SamplerService,
    configuration,
    default_matrix,
    editor_default_matrix,
    original,
)
from trajectory_remix.graph.storage import GraphStore


def sample(matrix, seed, pins=None):
    return task_axes.sample(original.sample_one, matrix, seed, None, pins)


def test_default_matrix_has_both_axes_and_metrics():
    m = editor_default_matrix()
    assert [x["id"] for x in m["dimensions"]["complexity"]] == [
        "trivial",
        "easy",
        "medium",
        "hard",
        "expert",
    ]
    assert [x["id"] for x in m["dimensions"]["task_length"]] == [
        "XXS",
        "XS",
        "S",
        "M",
        "L",
        "XL",
        "XXL",
    ]
    defs = configuration()["definitions"]
    assert defs["complexity"]["choices"][-1] == "expert"
    assert defs["task_length"]["choices"][0] == "XXS"


def test_length_weights_peak_in_the_middle_not_linear():
    weights = [x["weight"] for x in editor_default_matrix()["dimensions"]["task_length"]]
    peak = weights.index(max(weights))
    assert 0 < peak < len(weights) - 1
    assert weights[:peak] == sorted(weights[:peak])
    assert weights[peak:] == sorted(weights[peak:], reverse=True)


def test_scale_rules_move_to_task_length():
    m = editor_default_matrix()
    whens = [r["when"] for r in m["conditional_weights"]]
    assert not any("complexity" in w for w in whens)
    assert {"task_length": ["XL", "XXL"]} in whens and {"task_length": ["XXS"]} in whens
    supplied = task_axes.to_supplied(m)
    assert [x["id"] for x in supplied["dimensions"]["complexity"]][0] == "XXS"
    assert "task_length" not in supplied["dimensions"]
    original.validate_matrix(supplied)


def test_proposal_reports_both_axes_and_honours_pins():
    m = editor_default_matrix()
    p = sample(m, 5, {"complexity": "expert", "task_length": "XXS"})
    assert p["metrics"]["sampling"]["complexity"] == "expert"
    assert p["metrics"]["sampling"]["task_length"] == "XXS"
    assert p["scenario"]["project_state"]["task_length"] == "XXS"
    assert p["scenario"]["execution"]["suggested_task_count"] <= 2
    assert any("expert complexity" in r for r in p["generation_directive"]["requirements"])
    assert p["sampling_provenance"]["requested_overrides"] == {
        "complexity": "expert",
        "task_length": "XXS",
    }
    assert sample(m, 5) == sample(m, 5)
    with pytest.raises(ValueError):
        sample(m, 5, {"complexity": "small"})


def test_saved_unsplit_matrix_is_unchanged():
    legacy = default_matrix()
    assert sample(legacy, 137) == original.sample_one(legacy, 137, None, None)
    assert configuration(legacy)["definitions"]["complexity"]["choices"][1] == "small"


def test_coverage_compares_task_length(tmp_path):
    from trajectory_remix.graph.metrics import MetricService

    store = GraphStore.initialize(tmp_path / "t.sqlite")
    sampler = SamplerService(JobService(store))
    assert "task_length" in sampler.editor()["overrides"]
    report = MetricService(sampler).population({}, "all")
    rows = report["proposal_coverage"]["configured_target_comparison"]
    assert set(rows["task_length"]) == {"XXS", "XS", "S", "M", "L", "XL", "XXL"}
    assert set(rows["complexity"]) == {"trivial", "easy", "medium", "hard", "expert"}
