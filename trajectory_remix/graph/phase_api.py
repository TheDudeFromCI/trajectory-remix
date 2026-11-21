"""Routes for a run's phase barriers: read them, or skip the ones still waiting."""

from fastapi import HTTPException

from . import phase_barrier


def add_phase_routes(router, jobs, worker):
    def describe(job):
        j = jobs.inspect(job)
        enabled = phase_barrier.configured(j)
        waiting = phase_barrier.closed(enabled, phase_barrier.phase(jobs.store, job))
        return dict(
            enabled=bool(enabled),
            phase="generation" if waiting else "review",
            barriers={gate: gate in enabled for gate in phase_barrier.GATES},
            waiting=waiting,
        )

    @router.get("/jobs/{job}/phase")
    def get_phase(job: str):
        return describe(job)

    @router.post("/jobs/{job}/phase/review")
    def skip_barriers(job: str):
        j = jobs.inspect(job)
        if not phase_barrier.configured(j) or j["state"] not in {"running", "paused"}:
            raise HTTPException(409, "This job has no phase barrier to pass")
        phase_barrier.advance(jobs.store, job)
        jobs.append_log(
            job,
            "Barriers skipped: waiting trajectories continue now; unfinished ones carry on"
            " when their endpoint is available.",
        )
        if j["state"] == "running":
            worker.kick(job)
        return describe(job)
