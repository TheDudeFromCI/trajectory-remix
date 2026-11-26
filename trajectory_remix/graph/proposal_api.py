"""REST routes for inspecting, applying and discarding an Assistant proposal."""

from __future__ import annotations

from pydantic import Field

from . import assistant_proposals, proposal_review
from .models import DTO


class ApplyInput(DTO):
    expected_revision: int = Field(ge=1)


def add_proposal_routes(router, studio):
    @router.get("/trajectories/{ident}/assistant/proposal")
    def proposal(ident: str):
        studio.store.read(ident)  # Missing trajectories raise KeyError (404).
        return dict(proposal=assistant_proposals.summary(studio.store, ident))

    @router.post("/trajectories/{ident}/assistant/proposal/apply")
    def apply(ident: str, body: ApplyInput):
        saved, delta, resolved = proposal_review.apply(
            studio.commands, studio.comments, ident, body.expected_revision
        )
        return studio.present(saved) | dict(delta=delta, resolved=resolved)

    @router.delete("/trajectories/{ident}/assistant/proposal")
    def discard(ident: str):
        return proposal_review.discard(studio.store, studio.comments, ident)
