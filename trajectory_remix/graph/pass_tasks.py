"""MCP tasks for one trajectory's Draft and Assembly passes and its matrix metrics.

The same services as the Editor's Draft tab and Metrics tab (trajectory_passes): New draft
samples a proposal that keeps the trajectory's set metrics and runs the Draft pass; Assemble
runs the Assembly pass on the current graph, editing it in place. Both are model jobs.
"""

from __future__ import annotations

from pydantic import Field

from . import trajectory_passes
from .metrics import MetricService
from .models import DTO


class PassInput(DTO):
    id: str
    expected_revision: int = Field(ge=1)


class SampleInput(PassInput):
    seed: int | None = None


TASKS = {
    "draft_graph": (
        SampleInput,
        "Write a new draft for one trajectory with the Draft model (a job; model calls incur"
        " endpoint use). Its set category, label and target metrics are pins; unset ones are"
        " sampled from the matrix and assigned. Replaces metadata.draft; the graph is unchanged.",
    ),
    "assemble_graph": (
        PassInput,
        "Assemble one trajectory's draft into its graph with the Assembly model (a job; model"
        " calls incur endpoint use). Edits the existing graph in place; needs a draft.",
    ),
    "sample_graph_metrics": (
        SampleInput,
        "Fill one trajectory's unset category, label and target metrics from a matrix sample"
        " that keeps the set ones. No model calls.",
    ),
}


def call(studio, name, args):
    generation = studio.generation
    if name == "sample_graph_metrics":
        metrics = MetricService(generation.sampler)
        view = trajectory_passes.sample_unset(metrics, args.id, args.expected_revision, args.seed)
        return dict(
            revision=view["revision"],
            filled={key: view["values"][key]["observation"]["value"] for key in view["filled"]},
        )
    if name == "draft_graph":
        job, delta, filled = trajectory_passes.start_draft(
            generation, args.id, args.expected_revision, args.seed
        )
        response = dict(revision=delta["expected_revision"], filled=filled)
    else:
        job = trajectory_passes.start_assemble(generation, args.id, args.expected_revision)
        response = {}
    studio.worker.kick(job)
    return response | dict(job_id=job, status_url=f"/api/v1/jobs/{job}")
