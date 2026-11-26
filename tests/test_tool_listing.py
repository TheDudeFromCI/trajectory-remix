"""Tool schema listing: chosen tools plus a repeatable random selection of others."""

import pytest
from test_graph_generation import finish, service

from trajectory_remix.graph import tool_listing


def test_random_additions_are_repeatable_and_keep_chosen_tools():
    candidates = [f"t{i}" for i in range(40)]
    once = tool_listing.extras("trajectory-a", "tools", candidates, 0.5)
    assert once == tool_listing.extras("trajectory-a", "tools", candidates, 0.5)
    assert once != tool_listing.extras("trajectory-b", "tools", candidates, 0.5)
    assert 8 < len(once) < 32  # About half.
    assert tool_listing.extras("t", "tools", candidates, 0) == []
    assert tool_listing.extras("t", "tools", candidates, 1) == candidates


@pytest.mark.asyncio
async def test_assembly_writes_the_listing_before_the_opening(tmp_path):
    gen, fake = service(tmp_path, fast=False)
    job = gen.start([{"seed": 137}], {})
    await finish(gen, job)
    source = gen.store.read(gen.jobs.attempts(job)[0]["trajectory_id"]).source
    listing = source.events["tools"]
    assert listing.kind == "tool_schema" and source.windows[listing.window].purpose == "tools"
    assert source.events["ask"].after == ["tools"]
    assert tool_listing.presented_ids(listing.content) == {"chat", "read"}


def test_manual_listing_endpoint_lists_only_the_chosen_tools(tmp_path):
    from fastapi.testclient import TestClient

    from trajectory_remix.graph.studio_api import create_studio_app

    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings
    tools = {k: v.model_dump(mode="json") for k, v in settings.tools.items()}
    assert settings.tool_schema_extra_chance > 0
    app = create_studio_app(gen.store.path, artifact_root=tmp_path / "exports")
    with TestClient(app) as client:
        client.headers["X-Remix-Token"] = app.state.csrf
        body = dict(trajectory_id="traj-1", event_id="tools", tools=["read"])
        served = client.post("/api/v1/tools/listing", json=body).json()
        assert served == tool_listing.listing(tools, "traj-1", "tools", ["read"], 0)
        assert served["shown"] == ["read"] and served["added"] == []
        bad = client.post("/api/v1/tools/listing", json=dict(body, tools=["nope"]))
        assert bad.status_code == 422 and "Unknown tool IDs: nope" in bad.json()["message"]
