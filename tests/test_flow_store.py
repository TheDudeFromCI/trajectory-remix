"""The project flow lives in the project database, so a project stays one file."""

import pytest
import yaml
from test_adaptive_flow import episode
from test_graph_generation import finish, service

from trajectory_remix.graph import flow_document, flow_store
from trajectory_remix.graph.adaptive_flow import load_flow


def custom_flow(max_events):
    flow = load_flow()["flow"]
    flow["max_events"] = max_events
    return flow


def plan_flow(gen, job):
    return next(iter(gen.jobs.inspect(job)["config"]["config"]["plans"].values()))["flow"]


def test_saving_writes_no_file_and_new_runs_use_the_stored_flow(tmp_path):
    gen, _ = service(tmp_path, fast=False)
    assert flow_document.read_document(gen.store)["origin"] == "packaged"
    flow_document.save(gen.store, "packaged", flow=custom_flow(77))
    assert not flow_store.legacy_path(gen.store).exists()
    assert list(tmp_path.glob("*.yaml")) == []
    job = gen.start([{"seed": 1}], {})
    assert plan_flow(gen, job)["max_events"] == 77
    # A per-run override still wins over the stored flow.
    other = gen.start([{"seed": 1}], {"flow_yaml": yaml.safe_dump(custom_flow(5))})
    assert plan_flow(gen, other)["max_events"] == 5


def test_stale_revision_is_refused_and_keeps_the_stored_text(tmp_path):
    gen, _ = service(tmp_path)
    first = flow_document.save(gen.store, "packaged", flow=custom_flow(10))
    with pytest.raises(flow_store.FlowConflict):
        flow_document.save(gen.store, "packaged", flow=custom_flow(20))
    assert flow_document.read_document(gen.store)["revision"] == first["revision"]
    assert load_flow(text=flow_store.project_text(gen.store))["flow"]["max_events"] == 10


def test_legacy_flow_yaml_is_imported_once_and_left_untouched(tmp_path):
    gen, _ = service(tmp_path)
    legacy = flow_store.legacy_path(gen.store)
    text = "# legacy note\n" + yaml.safe_dump(custom_flow(33))
    legacy.write_text(text)
    document = flow_document.read_document(gen.store)
    assert document["origin"] == "project" and document["text"] == text
    legacy.write_text("edits: [after import are ignored")
    assert flow_store.project_text(gen.store) == text
    assert legacy.read_text().startswith("edits")  # The file itself is not changed or removed.


@pytest.mark.asyncio
async def test_running_jobs_keep_their_flow_when_the_project_flow_changes(tmp_path):
    fake = episode()
    gen, _ = service(tmp_path, fake, fast=False)
    job = gen.start([{"seed": 137}], {})
    before = plan_flow(gen, job)
    flow_document.save(gen.store, "packaged", flow=custom_flow(3))
    await finish(gen, job)
    assert plan_flow(gen, job) == before
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    assert source.metadata["stage"] == 0 and len(source.events) > 3
