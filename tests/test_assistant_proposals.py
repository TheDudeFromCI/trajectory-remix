"""Send to agent proposes changes: a person inspects them, then applies, discards or asks again."""

import pytest
from test_assistant import applied, comment, edit, flagged, proposed, reply, run, service, turn
from test_graph_studio import client, create  # noqa: F401  (pytest fixture)

from trajectory_remix.graph import assistant_memory, assistant_proposals, proposal_review
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.commands import GraphCommands
from trajectory_remix.graph.source_delta import ProposalConflict


def statuses(gen, record):
    return {c["id"]: c["status"] for c in gen.comments.for_trajectory(record.source.id)}


def live(gen, record):
    return gen.store.read(record.source.id)


def user_edit(gen, record, path, value):
    """A person edits the trajectory while the proposal waits."""
    current = live(gen, record)
    data = current.source.to_dict()
    section, key = path
    data[section][key] = value
    return gen.store.replace(record.source.id, data, current.revision)


@pytest.mark.asyncio
async def test_edits_wait_as_a_proposal_listing_the_changes(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Rewrite x.")
    fake.responses = [turn(edit(), reply(only, True, "Rewrote x."))]
    await run(gen, record)
    assert live(gen, record).source.events["x"].content == "before"
    summary = assistant_proposals.summary(gen.store, record.source.id)
    (change,) = summary["changes"]
    assert (change["kind"], change["id"], change["change"]) == ("event", "x", "changed")
    assert change["before"]["content"] == "before" and change["after"]["content"] == "after"
    assert summary["comment_ids"] == [only] and summary["conflicts"] == []
    assert statuses(gen, record) == {only: "addressed"}


@pytest.mark.asyncio
async def test_a_session_without_edits_proposes_nothing(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Is this right?")
    fake.responses = [turn(reply(only, False, "Yes, it is."))]
    await run(gen, record)
    assert assistant_proposals.load(gen.store, record.source.id) is None


@pytest.mark.asyncio
async def test_apply_merges_resolves_the_comments_and_can_be_undone(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Rewrite x.")
    fake.responses = [turn(edit(), reply(only, True, "Rewrote x."))]
    await run(gen, record)
    user_edit(gen, record, ("metadata", "note"), "mine")  # A different entity merges cleanly.
    current = live(gen, record)
    saved, delta, resolved = proposal_review.apply(
        GraphCommands(gen.store), gen.comments, record.source.id, current.revision
    )
    source = saved.record.source
    assert source.events["x"].content == "after" and source.metadata["note"] == "mine"
    assert resolved == 1 and statuses(gen, record) == {only: "resolved"}
    assert assistant_proposals.load(gen.store, record.source.id) is None
    BulkService(gen.jobs).restore([delta], undo=True)
    assert live(gen, record).source.events["x"].content == "before"


@pytest.mark.asyncio
async def test_a_conflicting_edit_blocks_apply_without_changing_anything(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Rewrite x.")
    fake.responses = [turn(edit(), reply(only, True, "Rewrote x."))]
    await run(gen, record)
    x = live(gen, record).source.events["x"].model_dump(mode="json")
    user_edit(gen, record, ("events", "x"), x | {"content": "mine"})
    summary = assistant_proposals.summary(gen.store, record.source.id)
    assert summary["conflicts"] == [dict(kind="event", id="x")]
    current = live(gen, record)
    with pytest.raises(ProposalConflict, match="event x"):
        proposal_review.apply(
            GraphCommands(gen.store), gen.comments, record.source.id, current.revision
        )
    assert live(gen, record).revision == current.revision
    assert proposed(gen, record)["events"]["x"]["content"] == "after"
    with pytest.raises(ProposalConflict):  # Sending again can't build on a conflict either.
        comment(gen, current, "More.")
        gen.start_assistant(record.source.id, current.revision, gen.comments)


@pytest.mark.asyncio
async def test_discard_reopens_the_comments_and_forgets_the_conversation(tmp_path):
    gen, fake = service(tmp_path)
    record, (only,) = flagged(gen, "Rewrite x.")
    fake.responses = [turn(edit(), reply(only, True, "Rewrote x."))]
    await run(gen, record)
    result = proposal_review.discard(gen.store, gen.comments, record.source.id)
    assert result == dict(discarded=True, reopened=1)
    (thread,) = gen.comments.for_trajectory(record.source.id)
    assert thread["status"] == "open"
    assert thread["replies"][-1]["body"] == proposal_review.DISCARD_NOTE
    assert assistant_memory.load(gen.store, record.source.id) is None
    assert live(gen, record).source.events["x"].content == "before"
    with pytest.raises(KeyError):
        proposal_review.discard(gen.store, gen.comments, record.source.id)


@pytest.mark.asyncio
async def test_request_changes_continues_from_the_proposal(tmp_path):
    gen, fake = service(tmp_path)
    record, (first,) = flagged(gen, "Rewrite x.")
    fake.responses = [turn(edit(), reply(first, True, "Rewrote x."))]
    await run(gen, record)
    base = assistant_proposals.load(gen.store, record.source.id)["base"]
    user_edit(gen, record, ("metadata", "note"), "mine")
    (second,) = comment(gen, live(gen, record), "Shorter, please.")
    fake.responses = [
        turn(("view_event", {"event_id": "x"}), edit("short"), reply(second, True, "Shortened."))
    ]
    await run(gen, live(gen, record))
    kept = assistant_memory.load(gen.store, record.source.id)
    viewed = [m for m in kept if m["role"] == "tool"][-3]
    assert '"after"' in viewed["content"], "the agent reads its own proposal, not the old source"
    proposal = assistant_proposals.load(gen.store, record.source.id)
    assert proposal["comment_ids"] == sorted([first, second])
    assert proposal["base"]["events"] == base["events"], "still one diff against the original"
    assert proposal["base"]["metadata"]["note"] == "mine", "rebased onto the person's edit"
    source = applied(gen, record)
    assert source.events["x"].content == "short" and source.metadata["note"] == "mine"
    assert set(statuses(gen, record).values()) == {"resolved"}


def test_rest_routes_show_apply_and_discard_a_proposal(client):  # noqa: F811
    created = create(client)
    ident = created["source"]["id"]
    assert client.get(f"/api/v1/trajectories/{ident}/assistant/proposal").json() == {
        "proposal": None
    }
    store = client.app.state.studio.store
    base = store.read(ident).source.to_dict()
    changed = base | {"metadata": base["metadata"] | {"note": "agent"}}
    for _ in range(2):
        assistant_proposals.save(
            store, ident, base_revision=1, base=base, source=changed, comment_ids=[]
        )
        shown = client.get(f"/api/v1/trajectories/{ident}/assistant/proposal").json()["proposal"]
        assert [(c["kind"], c["id"]) for c in shown["changes"]] == [("metadata", "note")]
    r = client.post(f"/api/v1/trajectories/{ident}/assistant/proposal/apply", json={})
    assert r.status_code == 422
    r = client.post(
        f"/api/v1/trajectories/{ident}/assistant/proposal/apply", json={"expected_revision": 1}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"]["metadata"]["note"] == "agent" and body["revision"] == 2
    assert body["delta"]["before"] == base and body["delta"]["expected_revision"] == 2
    assert client.delete(f"/api/v1/trajectories/{ident}/assistant/proposal").status_code == 404
    assistant_proposals.save(
        store, ident, base_revision=2, base=base, source=changed, comment_ids=[]
    )
    assert client.delete(f"/api/v1/trajectories/{ident}/assistant/proposal").json() == {
        "discarded": True,
        "reopened": 0,
    }
