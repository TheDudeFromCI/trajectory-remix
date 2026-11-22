"""Reviewer comments: dashboard REST and agent MCP tasks, outside the source."""

from test_graph_studio import client, create  # noqa: F401  (pytest fixture)


def comment(c, ident, kind, target, body="needs work"):
    target = {"kind": kind} | ({"id": target} if target else {})
    return c.post(f"/api/v1/trajectories/{ident}/comments", json={"target": target, "body": body})


def mcp(c, name, **arguments):
    r = c.post("/api/mcp/call", json={"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def test_comments_target_events_windows_and_trajectory(client):  # noqa: F811
    ident = create(client)["source"]["id"]
    event = comment(client, ident, "event", "b", "Closes the window too early.")
    assert event.status_code == 201, event.text
    row = event.json()
    assert row["target_kind"] == "event" and row["target_id"] == "b"
    assert row["author"] == "user" and row["status"] == "open" and row["source_revision"] == 1
    assert comment(client, ident, "window", "doc").status_code == 201
    assert comment(client, ident, "trajectory", None).status_code == 201
    items = client.get(f"/api/v1/trajectories/{ident}/comments").json()["items"]
    assert [i["target_kind"] for i in items] == ["event", "window", "trajectory"]
    assert all(i["target_exists"] for i in items)


def test_comment_targets_must_exist(client):  # noqa: F811
    ident = create(client)["source"]["id"]
    assert comment(client, ident, "event", "missing").status_code == 422
    assert comment(client, ident, "window", None).status_code == 422
    assert comment(client, ident, "trajectory", "x").status_code == 422
    assert comment(client, ident, "event", "b", "").status_code == 422
    assert comment(client, "no-such-trajectory", "trajectory", None).status_code == 404


def test_comments_leave_the_source_and_its_revision_alone(client):  # noqa: F811
    created = create(client)
    ident = created["source"]["id"]
    row = comment(client, ident, "event", "b").json()
    client.patch(f"/api/v1/comments/{row['id']}", json={"status": "resolved"})
    after = client.get(f"/api/v1/trajectories/{ident}").json()
    assert after["revision"] == 1 and after["intrinsic_hash"] == created["intrinsic_hash"]
    assert "comment" not in str(after["source"]["metadata"])


def test_resolve_reopen_edit_and_delete(client):  # noqa: F811
    ident = create(client)["source"]["id"]
    cid = comment(client, ident, "window", "doc").json()["id"]
    done = client.patch(
        f"/api/v1/comments/{cid}", json={"status": "resolved", "resolution": "Fixed it."}
    ).json()
    assert done["status"] == "resolved" and done["resolution"] == "Fixed it."
    reopened = client.patch(f"/api/v1/comments/{cid}", json={"status": "open"}).json()
    assert reopened["status"] == "open" and reopened["resolution"] is None
    edited = client.patch(f"/api/v1/comments/{cid}", json={"body": "Clearer."}).json()
    assert edited["body"] == "Clearer."
    assert client.patch(f"/api/v1/comments/{cid}", json={}).status_code == 422
    assert client.delete(f"/api/v1/comments/{cid}").json()["deleted"] is True
    assert client.delete(f"/api/v1/comments/{cid}").status_code == 404
    assert client.patch(f"/api/v1/comments/{cid}", json={"body": "x"}).status_code == 404


def test_deleted_targets_are_flagged_not_dropped(client):  # noqa: F811
    created = create(client)
    ident = created["source"]["id"]
    comment(client, ident, "event", "b")
    r = client.request(
        "DELETE", f"/api/v1/trajectories/{ident}/events/b", json={"expected_revision": 1}
    )
    assert r.status_code == 200, r.text
    (item,) = client.get(f"/api/v1/trajectories/{ident}/comments").json()["items"]
    assert item["target_exists"] is False


def test_project_listing_and_counts_skip_deleted_trajectories(client):  # noqa: F811
    first, second = create(client), create(client)
    comment(client, first["source"]["id"], "trajectory", None)
    comment(client, second["source"]["id"], "event", "a")
    comment(client, second["source"]["id"], "event", "b")
    counts = client.get("/api/v1/comments/open-counts").json()["counts"]
    assert counts == {first["source"]["id"]: 1, second["source"]["id"]: 2}
    page = client.get("/api/v1/comments?limit=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["next_offset"] == 2
    client.request(
        "DELETE", f"/api/v1/trajectories/{second['source']['id']}", json={"expected_revision": 1}
    )
    assert client.get("/api/v1/comments").json()["total"] == 1


def test_agent_reads_comments_answers_and_resolves(client):  # noqa: F811
    tools = {t["name"] for t in client.get("/api/mcp/tools").json()["tools"]}
    assert {"list_graph_comments", "add_graph_comment", "resolve_graph_comment"} <= tools
    ident = create(client)["source"]["id"]
    cid = comment(client, ident, "event", "b", "Event b closes the window too early.").json()["id"]
    queue = mcp(client, "list_graph_comments")
    assert [i["id"] for i in queue["items"]] == [cid]
    assert [c["id"] for c in mcp(client, "read_graph", id=ident)["open_comments"]] == [cid]
    asked = mcp(
        client,
        "add_graph_comment",
        trajectory=ident,
        target={"kind": "window", "id": "doc"},
        body="Should this window stay open?",
    )
    assert asked["author"] == "agent"
    resolved = mcp(client, "resolve_graph_comment", id=cid, resolution="Moved the close to c.")
    assert resolved["status"] == "resolved" and resolved["resolution"] == "Moved the close to c."
    mine = mcp(client, "list_graph_comments", trajectory=ident, status="all", limit=1)
    assert mine["total"] == 2 and mine["next_offset"] == 1
    assert mcp(client, "read_graph", id=ident)["open_comments"][0]["id"] == asked["id"]
    r = client.post(
        "/api/mcp/call",
        json={"name": "resolve_graph_comment", "arguments": {"id": cid, "resolution": ""}},
    )
    assert r.status_code == 422
