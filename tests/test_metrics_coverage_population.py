"""Coverage counts only accepted records, even when the population totals include everything."""

from uuid import uuid4

import pytest

from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.metrics import MetricService
from trajectory_remix.graph.models import GraphSource
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore


@pytest.fixture
def services(tmp_path):
    store = GraphStore.initialize(tmp_path / "test.sqlite")
    sampler = SamplerService(JobService(store))
    return store, sampler, MetricService(sampler)


def _source(metadata):
    return GraphSource(
        format="kidra-trajectory-graph/1",
        id=str(uuid4()),
        windows={"w": {"purpose": "reasoning", "agent_writable": True}},
        events={"a": {"kind": "agent_text", "window": "w", "content": "a"}},
        metadata=metadata,
    )


def test_all_population_coverage_excludes_unaccepted(services):
    store, sampler, metrics = services
    for stage in (-3, -3, 0, 1, 2):
        meta = sampler.metadata(sampler.propose(seed=137 + stage))
        meta["stage"] = stage
        store.create(_source(meta))
    report = metrics.population({}, "all")
    assert report["total"] == 5
    assert report["proposal_coverage"]["included_records"] == 2
