"""XXXS and XXXL task lengths: sampled, pinnable, and added once to matrices saved without them."""

from copy import deepcopy

import pytest

from trajectory_remix.graph import task_axes
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.matrix_migration import migrate
from trajectory_remix.graph.sampling import SamplerService, configuration, editor_default_matrix
from trajectory_remix.graph.storage import GraphStore


def old_matrix():
    """A split matrix as saved before the extremes existed."""
    m = editor_default_matrix()
    m.pop("task_length_version")
    m["dimensions"]["task_length"] = m["dimensions"]["task_length"][1:-1]
    for rule in m["conditional_weights"]:
        values = rule["when"].get("task_length")
        if values:
            rule["when"]["task_length"] = [v for v in values if v not in task_axes.EXTREMES]
    return m


@pytest.mark.parametrize("size", ["XXXS", "XXXL"])
def test_extremes_can_be_pinned(size, tmp_path):
    sampler = SamplerService(JobService(GraphStore.initialize(tmp_path / "p.sqlite")))
    for seed in range(40):
        p = sampler.propose(seed=seed, overrides={"task_length": size}, persist=False)
        assert p["proposal"]["metrics"]["sampling"]["task_length"] == size
    assert f"task length {size}" in p["proposal"]["generation_directive"]["requirements"][-1]


def test_a_saved_matrix_gains_the_extremes_and_their_neighbours_rules():
    m = old_matrix()
    assert task_axes.extend_lengths(m)
    ids = [x["id"] for x in m["dimensions"]["task_length"]]
    assert ids[0] == "XXXS" and ids[-1] == "XXXL" and len(ids) == 9
    whens = [r["when"] for r in m["conditional_weights"]]
    assert {"task_length": ["XXS", "XXXS"]} in whens
    assert {"task_length": ["XL", "XXL", "XXXL"]} in whens
    configuration(m)  # Still a valid matrix.
    assert not task_axes.extend_lengths(m), "only once"


def test_deleted_extremes_stay_deleted():
    m = editor_default_matrix()
    m["dimensions"]["task_length"] = m["dimensions"]["task_length"][1:]
    assert not task_axes.extend_lengths(m)
    assert m["dimensions"]["task_length"][0]["id"] == "XXS"


def test_startup_migration_saves_the_upgraded_matrix_once(tmp_path):
    store = GraphStore.initialize(tmp_path / "p.sqlite")
    assert not migrate(store), "the default matrix needs nothing"
    record = store.settings()
    data = record.settings.model_dump(mode="json")
    data["sampler_configuration"] = configuration(old_matrix(), {"domain": {"label": "Area"}})
    store.update_settings(data, record.revision)
    assert migrate(store)
    saved = store.settings().settings.sampler_configuration
    assert saved["definitions"]["task_length"]["choices"][-1] == "XXXL"
    assert saved["metric_options"] == {"domain": {"label": "Area"}}
    revision = store.settings().revision
    assert not migrate(store) and store.settings().revision == revision
    assert deepcopy(saved["matrix"])["task_length_version"] == task_axes.LENGTHS_VERSION
