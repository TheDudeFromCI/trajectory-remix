"""Flow editor document API: located diagnostics, lossless YAML and guarded saves."""

import copy

import pytest
import yaml
from fastapi.testclient import TestClient
from flow_fixtures import MINIMAL_FLOW
from test_graph_generation import service

from trajectory_remix.graph import flow_document
from trajectory_remix.graph.adaptive_flow import load_flow
from trajectory_remix.graph.studio_api import create_studio_app


@pytest.fixture
def client(tmp_path):
    gen, _ = service(tmp_path)
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        client.flow_path = gen.store.path.parent / "flow.yaml"
        yield client


def test_packaged_flow_round_trips_through_rendered_yaml():
    checked = flow_document.check(text=flow_document.packaged_text())
    assert checked["valid"] and checked["diagnostics"] == []
    assert checked["flow_hash"] == load_flow()["flow_hash"]
    again = flow_document.check(text=checked["yaml"])
    assert again["flow_hash"] == checked["flow_hash"]
    # Only the fields each kind uses are written.
    assert yaml.safe_load(checked["yaml"])["nodes"]["done"] == {"kind": "end"}


@pytest.mark.parametrize(
    "text",
    ["line one\nline two", "trailing space \nnext", "x" * 200, "a  b " * 30, "  lead", "#: {}"],
)
def test_rendered_strings_are_lossless(text):
    flow = load_flow()["flow"]
    flow["nodes"]["set_scene"]["instruction"] = text
    rendered = flow_document.render(flow)
    assert yaml.safe_load(rendered)["nodes"]["set_scene"]["instruction"] == text


def test_get_reports_origin_and_revision(client):
    packaged = client.get("/api/v1/generation/flow").json()
    assert packaged["origin"] == "packaged" and packaged["revision"] == "packaged"
    assert packaged["flow"]["start"] == "set_scene" and packaged["text"].startswith("version")


def test_check_locates_problems_on_nodes(client):
    flow = client.get("/api/v1/generation/flow").json()["flow"]
    flow["nodes"]["write_thought"]["next"] = "nowhere"
    flow["nodes"]["lonely"] = {"kind": "end"}
    result = client.post("/api/v1/generation/flow/check", json={"flow": flow}).json()
    assert not result["valid"] and result["flow"] is None
    problem = result["diagnostics"][0]
    assert (problem["node"], problem["field"], problem["code"]) == (
        "write_thought",
        "next",
        "missing_target",
    )
    flow["nodes"]["write_thought"]["next"] = "work"
    warned = client.post("/api/v1/generation/flow/check", json={"flow": flow}).json()
    assert warned["valid"]
    assert {(d["node"], d["code"]) for d in warned["diagnostics"]} >= {("lonely", "unreachable")}
    syntax = client.post("/api/v1/generation/flow/check", json={"yaml": "nodes: ["}).json()
    assert syntax["diagnostics"][0]["code"] == "yaml_syntax" and "line 1" in str(syntax)


def test_packaged_default_has_no_warnings_and_guards_its_end():
    nodes = load_flow()["flow"]["nodes"]
    assert flow_document.check(flow=load_flow()["flow"])["diagnostics"] == []
    kinds = {n["kind"] for n in nodes.values()}
    assert kinds == {"select", "event", "branch", "end"}
    assert sum(n["kind"] == "select" for n in nodes.values()) >= 3


def test_graph_warnings_catch_flows_that_would_misbehave():
    flow = copy.deepcopy(MINIMAL_FLOW)
    flow["nodes"]["write"]["next"] = "write"  # Event loops onto itself without a selection.
    flow["nodes"]["finished"]["then"] = "write"
    flow["nodes"]["choose"]["next"] = "done"  # End reached without the finished branch.
    codes = {(d["node"], d["code"]) for d in flow_document.check(flow=flow)["diagnostics"]}
    assert ("write", "stale_task") in codes and ("done", "unguarded_end") in codes
    flow = copy.deepcopy(MINIMAL_FLOW)
    flow["nodes"]["finished"].update(path="task.id", then="finished", otherwise="finished")
    codes = {d["code"] for d in flow_document.check(flow=flow)["diagnostics"]}
    assert "branch_cycle" in codes and "no_end" in codes


def test_save_writes_the_project_file_and_rejects_stale_revisions(client):
    loaded = client.get("/api/v1/generation/flow").json()
    flow = loaded["flow"]
    flow["max_events"] = 64
    saved = client.put(
        "/api/v1/generation/flow", json={"flow": flow, "expected_revision": loaded["revision"]}
    )
    assert saved.status_code == 200, saved.text
    document = saved.json()
    assert document["origin"] == "project" and document["flow"]["max_events"] == 64
    assert load_flow(client.flow_path)["flow"]["max_events"] == 64  # Future runs use it.
    stale = client.put(
        "/api/v1/generation/flow", json={"flow": flow, "expected_revision": loaded["revision"]}
    )
    assert stale.status_code == 409 and stale.json()["current_revision"] == document["revision"]
    flow["start"] = "missing"
    invalid = client.put(
        "/api/v1/generation/flow", json={"flow": flow, "expected_revision": document["revision"]}
    )
    assert invalid.status_code == 422
    assert load_flow(client.flow_path)["flow"]["start"] == "set_scene"


def test_raw_yaml_save_keeps_comments(client):
    loaded = client.get("/api/v1/generation/flow").json()
    text = "# Team flow, keep this note\n" + loaded["text"]
    saved = client.put(
        "/api/v1/generation/flow", json={"yaml": text, "expected_revision": loaded["revision"]}
    ).json()
    assert client.flow_path.read_text() == text and saved["text"] == text
    both = client.put(
        "/api/v1/generation/flow",
        json={"yaml": text, "flow": saved["flow"], "expected_revision": saved["revision"]},
    )
    assert both.status_code == 422
