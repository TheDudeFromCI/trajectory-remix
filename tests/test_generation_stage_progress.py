"""Generation counts each accepted stage across the frozen run."""

import pytest
from test_graph_generation import finish, service

from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.sampling import SamplerService


def progress(gen, job, completed, total):
    expected = {"completed": completed, "total": total}
    assert gen.jobs.inspect(job, include_config=False)["stage_progress"] == expected
    assert gen.jobs.summaries(limit=20, offset=0)[0]["stage_progress"] == expected


@pytest.mark.asyncio
async def test_five_attempts_count_twenty_stages_and_resume_from_saved_progress(tmp_path):
    gen, fake = service(tmp_path)
    job = gen.start([{"seed": seed} for seed in range(10, 15)], {})
    progress(gen, job, 0, 20)
    gen._admit(job)
    ident = gen.jobs.attempts(job)[0]["trajectory_id"]
    for index, (endpoint, operation) in enumerate(
        [
            ("g", "draft"),
            ("g", "assembly"),
            ("f", "review"),
            ("c", "review"),
        ],
        1,
    ):
        gen._prepare(job, ident)
        if operation == "review" and endpoint == "f":
            gen._prepare(job, ident)  # Move signed-off Assembly into its next phase.
        ticket = gen.jobs.claim(endpoint)
        assert ticket.payload["operation"] == operation
        gen.jobs.request_result(ticket, await fake(ticket))
        progress(gen, job, index - 1, 20)
        gen.consume(ticket)
        progress(gen, job, index, 20)
    record = gen.store.read(ident)
    assert gen.jobs.inspect(job)["progress"] == 1
    edited = record.source.to_dict()
    edited["metadata"].pop("evaluations")
    gen.store.replace(ident, edited, record.revision)
    progress(gen, job, 4, 20)
    jobs = JobService(gen.store)
    restarted = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), fake)
    progress(restarted, job, 4, 20)
    jobs.control(job, "resume")
    await finish(restarted, job)
    progress(restarted, job, 20, 20)


@pytest.mark.asyncio
async def test_skipped_roles_and_failed_assembly_keep_exact_stage_counts(tmp_path):
    gen, fake = service(tmp_path, max_authoring_exchanges=1)
    snapshot = gen.store.settings()
    snapshot.settings.endpoint_roles.pop("fast")
    gen.store.update_settings(snapshot.settings, snapshot.revision)
    job = gen.start([{"seed": 40}], {})
    progress(gen, job, 0, 3)

    def no_signoff(ticket):
        return {"role": "assistant", "content": "Still working."}

    fake.responses = [{"draft": "Complete working notes. " * 20}, no_signoff]
    await finish(gen, job)
    assert gen.jobs.inspect(job)["state"] == "completed"
    assert gen.jobs.inspect(job)["progress"] == 1
    progress(gen, job, 1, 3)
