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
    done = client.patch(f"/api/v1/comments/{cid}", json={"status": "resolved"}).json()
    assert done["status"] == "resolved"
    reopened = client.patch(f"/api/v1/comments/{cid}", json={"status": "open"}).json()
    assert reopened["status"] == "open"
    patch = client.patch(f"/api/v1/comments/{cid}", json={"status": "addressed"})
    assert patch.status_code == 422, "only agents mark a comment addressed"
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
    counts = client.get("/api/v1/comments/counts").json()["counts"]
    assert counts == {
        first["source"]["id"]: {"open": 1, "addressed": 0},
        second["source"]["id"]: {"open": 2, "addressed": 0},
    }
    page = client.get("/api/v1/comments?limit=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["next_offset"] == 2
    client.request(
        "DELETE", f"/api/v1/trajectories/{second['source']['id']}", json={"expected_revision": 1}
    )
    assert client.get("/api/v1/comments").json()["total"] == 1


def test_agent_addresses_and_only_the_user_resolves(client):  # noqa: F811
    tools = {t["name"] for t in client.get("/api/mcp/tools").json()["tools"]}
    assert {"list_graph_comments", "add_graph_comment", "address_graph_comment"} <= tools
    assert "resolve_graph_comment" not in tools
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
    done = mcp(client, "address_graph_comment", id=cid, note="Moved the close to c.")
    assert done["status"] == "addressed"
    assert done["replies"][-1]["author"] == "agent"
    assert done["replies"][-1]["body"] == "Moved the close to c."
    assert [i["id"] for i in mcp(client, "list_graph_comments")["items"]] == [asked["id"]]
    waiting = mcp(client, "list_graph_comments", status="addressed")["items"]
    assert [i["id"] for i in waiting] == [cid]
    mine = mcp(client, "list_graph_comments", trajectory=ident, status="all", limit=1)
    assert mine["total"] == 2 and mine["next_offset"] == 1
    assert mcp(client, "read_graph", id=ident)["open_comments"][0]["id"] == asked["id"]
    r = client.post(
        "/api/mcp/call",
        json={"name": "address_graph_comment", "arguments": {"id": cid, "note": ""}},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/mcp/call",
        json={"name": "reply_graph_comment", "arguments": {"id": cid, "body": "x", "reopen": True}},
    )
    assert r.status_code == 422, "agents cannot reopen through a reply"
    # The user sends it back: one reply that reopens it, then the agent sees it again.
    back = client.post(
        f"/api/v1/comments/{cid}/replies", json={"body": "c still reads it.", "reopen": True}
    ).json()
    assert back["status"] == "open" and back["replies"][-1]["author"] == "user"
    assert cid in [i["id"] for i in mcp(client, "list_graph_comments")["items"]]
    # Accepting a fix is the user's resolve.
    mcp(client, "address_graph_comment", id=cid, note="Now c closes it.")
    accepted = client.patch(f"/api/v1/comments/{cid}", json={"status": "resolved"}).json()
    assert accepted["status"] == "resolved"


def test_reply_threads(client):  # noqa: F811
    ident = create(client)["source"]["id"]
    cid = comment(client, ident, "event", "b", "Why does b close the window?").json()["id"]
    r = client.post(f"/api/v1/comments/{cid}/replies", json={"body": "Event c still needs it."})
    assert r.status_code == 201, r.text
    assert [x["body"] for x in r.json()["replies"]] == ["Event c still needs it."]
    answer = mcp(client, "reply_graph_comment", id=cid, body="Should c own the close instead?")
    assert [x["author"] for x in answer["replies"]] == ["user", "agent"]
    assert answer["status"] == "open", "a reply never changes the comment's status"
    (listed,) = client.get(f"/api/v1/trajectories/{ident}/comments").json()["items"]
    assert len(listed["replies"]) == 2
    assert len(mcp(client, "list_graph_comments")["items"][0]["replies"]) == 2
    first = listed["replies"][0]["id"]
    left = client.delete(f"/api/v1/comments/{cid}/replies/{first}").json()
    assert [x["author"] for x in left["replies"]] == ["agent"]
    assert client.delete(f"/api/v1/comments/{cid}/replies/{first}").status_code == 404
    assert client.post(f"/api/v1/comments/{cid}/replies", json={"body": ""}).status_code == 422
    assert client.post("/api/v1/comments/nope/replies", json={"body": "x"}).status_code == 404
    client.delete(f"/api/v1/comments/{cid}")
    with client.app.state.studio.store.connect() as db:
        assert db.execute("SELECT count(*) FROM graph_comment_replies").fetchone()[0] == 0


def test_open_comments_filter_in_shared_selector(client):  # noqa: F811
    first, second = create(client), create(client)
    flagged = comment(client, first["source"]["id"], "event", "a").json()
    resolved = comment(client, second["source"]["id"], "event", "a").json()
    client.patch(f"/api/v1/comments/{resolved['id']}", json={"status": "resolved"})
    listing = client.get("/api/v1/trajectories?comments=open").json()
    assert [i["id"] for i in listing["items"]] == [first["source"]["id"]]
    assert client.get("/api/v1/trajectories").json()["total"] == 2
    assert client.get("/api/v1/trajectories?comments=all").status_code == 422
    found = mcp(client, "list_graphs", query={"comments": "open"})
    assert [i["id"] for i in found["items"]] == [first["source"]["id"]]
    lines = client.get("/api/v1/trajectories/export?comments=open").text.strip().splitlines()
    assert len(lines) == 1
    client.app.state.studio.comments.address(flagged["id"], "Fixed.")
    assert client.get("/api/v1/trajectories?comments=open").json()["total"] == 0
    for status in ("addressed", "unresolved"):
        found = client.get(f"/api/v1/trajectories?comments={status}").json()["items"]
        assert [i["id"] for i in found] == [first["source"]["id"]]
    client.patch(f"/api/v1/comments/{flagged['id']}", json={"status": "resolved"})
    assert client.get("/api/v1/trajectories?comments=unresolved").json()["total"] == 0


def test_tables_from_before_the_addressed_state_are_upgraded(tmp_path):
    import uuid

    from test_graph_studio import source

    from trajectory_remix.graph.comments import CommentService
    from trajectory_remix.graph.storage import GraphStore

    store = GraphStore.initialize(tmp_path / "old.sqlite")
    data = source()
    store.create(data)
    with store.connect() as db:
        db.executescript(
            """CREATE TABLE graph_comments (id TEXT PRIMARY KEY, trajectory_id TEXT NOT NULL,
            target_kind TEXT NOT NULL, target_id TEXT, body TEXT NOT NULL, author TEXT NOT NULL,
            source_revision INTEGER,
            status TEXT NOT NULL CHECK(status IN ('open','resolved')), resolution TEXT,
            created TEXT NOT NULL, updated TEXT NOT NULL);"""
        )
        db.execute(
            "INSERT INTO graph_comments VALUES(?,?,'trajectory',NULL,'Old.','user',1,"
            "'resolved','Agent fixed it.','2026-01-01','2026-01-02')",
            (str(uuid.uuid4()), data["id"]),
        )
    service = CommentService(store)
    (old,) = service.for_trajectory(data["id"])
    assert old["status"] == "resolved" and "resolution" not in old
    assert [(r["author"], r["body"]) for r in old["replies"]] == [("agent", "Agent fixed it.")]
    service.address(old["id"], "Addressed works on the upgraded table.")
    CommentService(store)  # Upgrading twice is a no-op.
