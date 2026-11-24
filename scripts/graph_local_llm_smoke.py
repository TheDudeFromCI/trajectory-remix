"""USER-MACHINE ONLY: a small Draft and Assembly smoke run against your configured endpoints.

No benchmarks, CI hook, hidden hosted endpoint, tool execution or compilation.
Invoke explicitly; merely importing/listing cases never calls a model.
"""

import argparse
import asyncio
import json
import platform
import subprocess
from pathlib import Path

from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.models import ProjectSettings
from trajectory_remix.graph.provider import OpenAITransport
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.user_validation import require_user_machine


async def run(args):
    require_user_machine(args.user_machine)
    settings = ProjectSettings.model_validate_json(args.config.read_text())
    if not args.output.is_dir():
        args.output.mkdir(parents=True)
    path = args.output / "project.sqlite"
    GraphStore.initialize(path, settings)  # Refuse overwriting a previous run.
    store = GraphStore(path)
    jobs = JobService(store, active_limit=2)
    gen = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), OpenAITransport())
    results = []
    report = dict(
        acceptance_passed=False,
        quality="requires human inspection",
        performance="not measured",
        python=platform.python_version(),
        seed=args.seed,
        count=args.count,
        models={
            key: dict(model=e.model, concurrency=e.concurrency, retries=e.retries)
            for key, e in settings.endpoints.items()
        },
        endpoint_roles=settings.endpoint_roles,
        generation=settings.generation.model_dump(),
        results=results,
    )
    try:
        report["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        report["commit"] = "unknown (installed package)"
    requests = [{"seed": args.seed + offset} for offset in range(args.count)]
    job = gen.start(requests, {})
    task = asyncio.create_task(gen.run(jobs, job))
    while not task.done():
        await asyncio.sleep(0.2)
        status = jobs.inspect(job)
        if status["paused_endpoints"] or status["state"] == "paused":
            if status["state"] == "running":
                jobs.control(job, "pause")
            print(
                "Paused; inspect project.sqlite in graph-studio and explicitly Resume."
                " No quality failure inferred."
            )
            break
    await task
    for attempt in jobs.attempts(job):
        try:
            source = store.read(attempt["trajectory_id"]).source
        except KeyError:
            source = None
        results.append(
            dict(
                job=job,
                attempt=attempt["id"],
                trajectory=attempt["trajectory_id"],
                state=jobs.inspect(job)["state"],
                outcome=attempt["outcome"],
                stage=source.metadata.get("stage") if source else None,
                review_outcomes=source.metadata.get("review_outcomes", []) if source else [],
                evaluations=source.metadata.get("evaluations", []) if source else [],
            )
        )
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(
        f"Inspect {path} and {args.output / 'report.json'}. Quality needs human sample inspection; no benchmark was performed."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-machine", action="store_true")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=3, help="Trajectories to generate")
    parser.add_argument("--seed", type=int, default=137)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except ValueError as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
