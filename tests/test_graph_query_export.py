"""Bulk "Export": stream all trajectories matching a search as JSON Lines."""

import json

from test_graph_studio import client, create  # noqa: F401


def test_export_matches_streams_all_matching_sources(client):  # noqa: F811
    ids = {create(client)["source"]["id"] for _ in range(3)}
    response = client.get("/api/v1/trajectories/export")
    assert response.status_code == 200
    assert "trajectories.jsonl" in response.headers["content-disposition"]
    assert {json.loads(line)["id"] for line in response.text.splitlines()} == ids
    assert client.get("/api/v1/trajectories/export?stage=2").text == ""
    assert client.get("/api/v1/trajectories/export?split=Nope").status_code >= 400
