"""Sampler workbench pins on any field, and the Draft-tab passes as MCP tools."""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_graph_generation import DRAFT, Fake, settings

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "api.sqlite"
    GraphStore.initialize(path, settings(fast=False))
    app = create_studio_app(path, provider=Fake())
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        yield client


def settle(client, job):
    for _ in range(500):
        state = client.get(f"/api/v1/jobs/{job}").json()["state"]
        if state in {"completed", "paused", "cancelled"}:
            return state
        time.sleep(0.01)
    raise AssertionError("job did not settle")


def mcp(client, name, **arguments):
    response = client.post("/api/mcp/call", json={"name": name, "arguments": arguments})
    return response


def test_the_workbench_offers_a_pin_for_every_category(client):
    overrides = client.get("/api/v1/sampler").json()["overrides"]
    for key in ("stakes", "incident", "focus.behavior", "decision.choice", "team_posture"):
        assert overrides[key], key
    assert "high" in overrides["stakes"]


def test_previews_and_runs_keep_any_pin(client):
    pins = {"stakes": "high", "incident": "stale_decision", "focus.behavior": "strategy_revision"}
    preview = client.post("/api/v1/sampler/proposals", json={"seed": 4, "overrides": pins})
    assert preview.status_code == 201, preview.text
    sampled = preview.json()["proposal"]["metrics"]["sampling"]
    assert {k: sampled[k] for k in pins} == pins
    assert client.get(f"/api/v1/sampler/proposals/{preview.json()['id']}").status_code == 200
    run = client.post(
        "/api/v1/generation/runs",
        json={"requests": [{"seed": 9, "overrides": pins}], "instructions": {}},
    )
    assert run.status_code == 202, run.text
    assert settle(client, run.json()["job_id"]) == "completed"
    (attempt,) = client.get(f"/api/v1/jobs/{run.json()['job_id']}/attempts").json()
    source = client.get(f"/api/v1/trajectories/{attempt['trajectory_id']}").json()["source"]
    proposal = source["metadata"]["proposal"]["proposal"]
    assert {k: proposal["metrics"]["sampling"][k] for k in pins} == pins
    assert proposal["sampling_provenance"]["pinned"] == pins


def test_incompatible_or_unknown_pins_are_refused(client):
    clash = {"entry_state": "fresh", "lifecycle": "active"}
    response = client.post("/api/v1/sampler/proposals", json={"seed": 1, "overrides": clash})
    assert response.status_code == 422 and "requires lifecycle" in response.text
    run = client.post(
        "/api/v1/generation/runs",
        json={"requests": [{"seed": 1, "overrides": clash}], "instructions": {}},
    )
    assert run.status_code == 422
    unknown = client.post(
        "/api/v1/sampler/proposals", json={"seed": 1, "overrides": {"stakes": "nope"}}
    )
    assert unknown.status_code == 422 and "Unknown pin stakes=nope" in unknown.text


def created(client, metrics=None):
    source = dict(
        format="kidra-trajectory-graph/1",
        id=str(uuid4()),
        windows={"reason": {"purpose": "reasoning", "agent_writable": True}},
        events={},
        metadata=dict(stage=-3, metrics=metrics or {}),
    )
    return client.post("/api/v1/trajectories", json=source).json()


def test_mcp_drafts_assembles_and_samples_one_trajectory(client):
    tools = {t["name"] for t in client.get("/api/mcp/tools").json()["tools"]}
    assert {"draft_graph", "assemble_graph", "sample_graph_metrics"} <= tools
    record = created(client, {"stakes": dict(value="high", origin="manual_annotation")})
    ident = record["source"]["id"]

    drafted = mcp(client, "draft_graph", id=ident, expected_revision=record["revision"], seed=3)
    assert drafted.status_code == 200, drafted.text
    assert "lifecycle" in drafted.json()["filled"] and "stakes" not in drafted.json()["filled"]
    assert settle(client, drafted.json()["job_id"]) == "completed"
    saved = client.get(f"/api/v1/trajectories/{ident}").json()
    assert saved["source"]["metadata"]["draft"] == DRAFT
    assert saved["source"]["metadata"]["metrics"]["stakes"]["value"] == "high"

    built = mcp(client, "assemble_graph", id=ident, expected_revision=saved["revision"])
    assert built.status_code == 200, built.text
    assert settle(client, built.json()["job_id"]) == "completed"
    assembled = client.get(f"/api/v1/trajectories/{ident}").json()
    assert assembled["source"]["events"] and assembled["source"]["metadata"]["stage"] == 0

    other = created(client, {"task_length": dict(value="XL", origin="manual_annotation")})
    sampled = mcp(
        client,
        "sample_graph_metrics",
        id=other["source"]["id"],
        expected_revision=other["revision"],
    )
    assert sampled.status_code == 200, sampled.text
    filled = sampled.json()["filled"]
    assert "task_length" not in filled and 12 <= filled["suggested_task_count"] <= 22


def test_mcp_refuses_assembly_without_a_draft(client):
    record = created(client)
    response = mcp(
        client, "assemble_graph", id=record["source"]["id"], expected_revision=record["revision"]
    )
    assert response.status_code == 422 and "draft before assembling" in response.text
