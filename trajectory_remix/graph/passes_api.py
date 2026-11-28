"""REST routes for the Editor's Draft tab and the Metrics tab's matrix sampling."""

from __future__ import annotations

from pydantic import Field

from . import trajectory_passes
from .models import DTO


class PassInput(DTO):
    expected_revision: int = Field(ge=1)


class SampleInput(PassInput):
    # Repeatable samples for tests and scripts; omitted draws a fresh random seed.
    seed: int | None = None


def add_pass_routes(router, studio, generation, metrics):
    def queued(job):
        studio.worker.kick(job)
        return dict(job_id=job, status_url=f"/api/v1/jobs/{job}")

    @router.post("/trajectories/{ident}/draft", status_code=202)
    async def new_draft(ident: str, body: SampleInput):
        """Assign sampled values to unset metrics, then run the Draft pass."""
        job, delta, filled = trajectory_passes.start_draft(
            generation, ident, body.expected_revision, body.seed
        )
        return queued(job) | dict(delta=delta, filled=filled)

    @router.post("/trajectories/{ident}/assemble", status_code=202)
    async def assemble(ident: str, body: PassInput):
        """Run the Assembly pass on the draft, editing the existing graph in place."""
        return queued(trajectory_passes.start_assemble(generation, ident, body.expected_revision))

    @router.post("/metrics/trajectories/{ident}/sample-unset")
    def sample_unset(ident: str, body: SampleInput):
        return trajectory_passes.sample_unset(metrics, ident, body.expected_revision, body.seed)
