"""A person's decision on an Assistant proposal: apply it or discard it.

Apply merges the proposal onto the trajectory as one ordinary edit (so Undo reverts it) and
resolves the comments it addressed. A person may apply only some of its changes; the rest are
dropped with the proposal. Discard drops it, reopens those comments with a note and
forgets the conversation, so the next session starts from the trajectory as it is.
"""

from __future__ import annotations

from . import assistant_memory, assistant_proposals, source_delta
from .comments import CommentEdit, NewReply

DISCARD_NOTE = "Discarded the agent's proposed changes."


def apply(commands, comments, trajectory_id, expected_revision, only=None):
    """Returns the saved result and the Undo delta. `only` lists the (kind, id) changes to
    apply; None applies them all. Raises ProposalConflict, RevisionConflict or ValueError
    without changing anything."""
    store = commands.store
    proposal = assistant_proposals.load(store, trajectory_id)
    if proposal is None:
        raise KeyError(trajectory_id)
    before = store.read(trajectory_id).source.to_dict()
    chosen = proposal["source"]
    if only is not None:
        chosen = source_delta.pick(proposal["base"], chosen, only)
    merged = source_delta.merge(proposal["base"], before, chosen)
    saved = commands.replace(
        trajectory_id,
        merged,
        expected_revision,
        provenance=dict(action="assistant_proposal", base_revision=proposal["base_revision"]),
    )
    assistant_proposals.forget(store, trajectory_id)
    resolved = _each_addressed(comments, proposal, lambda c: _resolve(comments, c))
    record = saved.record
    delta = dict(
        id=trajectory_id,
        before=before,
        after=record.source.to_dict(),
        expected_revision=record.revision,
        expected_commit=record.commit_id,
    )
    return saved, delta, resolved


def discard(store, comments, trajectory_id):
    proposal = assistant_proposals.load(store, trajectory_id)
    if proposal is None:
        raise KeyError(trajectory_id)
    assistant_proposals.forget(store, trajectory_id)
    assistant_memory.forget(store, trajectory_id)
    reopened = _each_addressed(
        comments,
        proposal,
        lambda c: comments.reply(c, NewReply(body=DISCARD_NOTE, reopen=True), "user"),
    )
    return dict(discarded=True, reopened=reopened)


def _resolve(comments, ident):
    comments.edit(ident, CommentEdit(status="resolved"))


def _each_addressed(comments, proposal, act):
    """Run act on each of the proposal's comments still awaiting review; returns how many.
    Comments deleted or already decided meanwhile are left alone."""
    count = 0
    wanted = set(proposal["comment_ids"])
    for comment in comments.for_trajectory(proposal["source"]["id"], "addressed"):
        if comment["id"] in wanted:
            act(comment["id"])
            count += 1
    return count
