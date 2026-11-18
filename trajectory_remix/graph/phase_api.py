"""Routes for a run's phase barrier: read the phase, or start the reviews early."""

from fastapi import HTTPException

from . import phase_barrier

NAMES = {phase_barrier.GENERATION: "generation", phase_barrier.REVIEW: "review"}


def _enabled(job):
    config = job.get("config") or {}
    settings = config.get("settings") or config.get("options", {}).get("settings") or {}
    return job["kind"] == "run" and bool(settings.get("generation", {}).get("phase_barrier"))


def add_phase_routes(router, jobs, worker):
    def describe(job):
        j = jobs.inspect(job)
        return dict(enabled=_enabled(j), phase=NAMES[phase_barrier.phase(jobs.store, job)])

    @router.get("/jobs/{job}/phase")
    def get_phase(job: str):
        return describe(job)

    @router.post("/jobs/{job}/phase/review")
    def start_reviews(job: str):
        j = jobs.inspect(job)
        if not _enabled(j) or j["state"] not in {"running", "paused"}:
            raise HTTPException(409, "This job has no phase barrier to pass")
        phase_barrier.advance(jobs.store, job)
        jobs.append_log(
            job,
            "Reviews started early: assembled trajectories go to review now; unfinished ones"
            " continue Draft and Assembly when their endpoint is available.",
        )
        if j["state"] == "running":
            worker.kick(job)
        return describe(job)
