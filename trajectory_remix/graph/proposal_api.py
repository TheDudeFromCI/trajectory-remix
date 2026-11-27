"""REST routes for inspecting, applying and discarding an Assistant proposal."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from . import assistant_proposals, proposal_review
from .models import DTO


class ChangeRef(DTO):
    kind: Literal["defaults", "window", "event", "metadata"]
    id: str | None = None


class ApplyInput(DTO):
    expected_revision: int = Field(ge=1)
    # The changes to apply; omitted applies them all. The others are dropped.
    changes: list[ChangeRef] | None = Field(default=None, min_length=1)


def add_proposal_routes(router, studio):
    @router.get("/trajectories/{ident}/assistant/proposal")
    def proposal(ident: str):
        studio.store.read(ident)  # Missing trajectories raise KeyError (404).
        return dict(proposal=assistant_proposals.summary(studio.store, ident))

    @router.post("/trajectories/{ident}/assistant/proposal/apply")
    def apply(ident: str, body: ApplyInput):
        only = None if body.changes is None else [(c.kind, c.id) for c in body.changes]
        saved, delta, resolved = proposal_review.apply(
            studio.commands, studio.comments, ident, body.expected_revision, only
        )
        return studio.present(saved) | dict(delta=delta, resolved=resolved)

    @router.delete("/trajectories/{ident}/assistant/proposal")
    def discard(ident: str):
        return proposal_review.discard(studio.store, studio.comments, ident)
