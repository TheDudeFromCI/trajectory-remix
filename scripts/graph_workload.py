"""USER MACHINE ONLY: 25k stored / 20 active acceptance workload.

Explicit opt-in, no import side effects, no CI invocation. Fake provider by
 default; local provider requires a supplied config. Never starts a model server.
"""

import argparse
import asyncio
import json
import platform
import socket
import subprocess
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
import uvicorn

from trajectory_remix.graph.models import ProjectSettings
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app
from trajectory_remix.graph.user_validation import require_user_machine


class FakeProvider:
    """Schema-shaped fixed observations/verdicts, never a quality assessment."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, ticket):
        self.calls += 1
        await asyncio.sleep(0.05)  # Simulated I/O, not a claimed provider latency.
        payload = ticket.payload
        if payload["operation"] == "event":
            props = payload["schema"]["properties"]
            output = (
                {"arguments": {"content": "Synthetic workload reply"}}
                if "arguments" in props
                else {"content": "Synthetic workload observation"}
            )
        else:
            schema = payload["schema"]
            if payload["operation"] == "repair":
                schema = schema["oneOf"][2]["properties"]["review"]
            review = dict(
                summary="Synthetic fixture verdict; no quality certification",
                items={
                    key: dict(status="pass", evidence="Synthetic workload evidence", event_ids=[])
                    for key in schema["properties"]["items"]["properties"]
                },
            )
            output = (
                dict(action="verdict", review=review)
                if payload["operation"] == "repair"
                else review
            )
        return dict(output=output, model="offline-workload-fixture")


def fixture(index):
    return dict(
        format="kidra-trajectory-graph/1",
        id=str(uuid5(NAMESPACE_URL, f"remix-workload/{index}")),
        windows={"doc": dict(purpose="document", agent_writable=False)},
        events={
            "input": dict(
                kind="external_text",
                window="doc",
                content=f"Stored fixture {index}",
                close_window=True,
            )
        },
        metadata=dict(stage=0, split="Auto", workload_fixture=True),
    )


async def run(args):
    require_user_machine(args.user_machine)
    if args.output.exists():
        raise ValueError("Use a fresh output directory; existing evidence is never overwritten")
    if args.provider == "local" and args.config is None:
        raise ValueError("--provider local requires --config; no endpoint is guessed")
    settings = (
        ProjectSettings.model_validate_json(args.config.read_text())
        if args.config
        else ProjectSettings()
    )
    fake = None
    if args.provider == "fake":
        # Discard configured network endpoints entirely in fake mode.
        data = settings.model_dump(mode="json")
        data.update(
            endpoints={
                key: dict(
                    url="http://127.0.0.1:1/v1",
                    model="offline-fixture",
                    concurrency=args.active,
                    retries=0,
                )
                for key in ("draft", "fast", "critical")
            },
            endpoint_roles={key: key for key in ("draft", "fast", "critical")},
        )
        data["tools"].setdefault(
            "chat",
            dict(
                name="Chat",
                input_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            ),
        )
        settings = ProjectSettings.model_validate(data)
        fake = FakeProvider()
    elif "draft" not in settings.endpoint_roles:
        raise ValueError("Local configuration requires a Draft role")
    args.output.mkdir(parents=True)
    GraphStore.initialize(args.output / "project.sqlite", settings)
    app = create_studio_app(args.output / "project.sqlite", provider=fake, active_limit=args.active)
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    sock.listen()
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    started = time.perf_counter()
    report = dict(
        acceptance_passed=False,
        quality="unassessed",
        provider=args.provider,
        python=platform.python_version(),
        platform=platform.platform(),
        stored_target=args.stored,
        active_target=args.active,
        attempts=args.attempts,
        seed=args.seed,
        samples=[],
        outcomes=[],
        jobs=[],
    )
    try:
        report["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        report["commit"] = "unknown (installed package)"
    report["models"] = {
        key: dict(model=e.model, concurrency=e.concurrency, retries=e.retries)
        for key, e in settings.endpoints.items()
    }

    def save():
        (args.output / "report.json").write_text(json.dumps(report, indent=2))

    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("Studio failed to start")
            await asyncio.sleep(0.05)
        print(f"Studio {url}; project and report: {args.output}. Open the UI during the run.")
        async with httpx.AsyncClient(
            base_url=url, timeout=120, headers={"X-Remix-Token": app.state.csrf}
        ) as client:

            async def api(method, path, body=None):
                r = await client.request(method, "/api/v1" + path, json=body)
                r.raise_for_status()
                return r.json()

            async def settle(job):
                while True:
                    status = await api("GET", f"/jobs/{job}?include_config=false")
                    with app.state.jobs.store.connect() as db:
                        active = db.execute(
                            "SELECT count(*) FROM graph_owners WHERE mode='attached'"
                        ).fetchone()[0]
                    report["samples"].append(
                        dict(
                            elapsed_seconds=time.perf_counter() - started,
                            job=job,
                            active=active,
                            state=status["state"],
                            progress=status["progress"],
                            total=status["total"],
                            paused_endpoints=status["paused_endpoints"],
                        )
                    )
                    save()
                    if status["state"] != "running" or status["paused_endpoints"]:
                        return status
                    await asyncio.sleep(args.poll)

            # Upload bounded JSONL batches through the actual import worker.
            for offset in range(0, args.stored, 100):
                text = "\n".join(
                    json.dumps(fixture(i)) for i in range(offset, min(offset + 100, args.stored))
                )
                job = (await api("POST", "/imports", dict(text=text)))["job_id"]
                report["jobs"].append(job)
                if (await settle(job))["state"] != "completed":
                    raise RuntimeError("Import paused; inspect retained logs, then Resume manually")
            report["stored_before_generation"] = (await api("GET", "/trajectories?limit=1"))[
                "total"
            ]
            for offset in range(0, args.attempts, 1000):
                requests = [
                    dict(seed=args.seed + i)
                    for i in range(offset, min(offset + 1000, args.attempts))
                ]
                job = (
                    await api("POST", "/generation/runs", dict(requests=requests, instructions={}))
                )["job_id"]
                report["jobs"].append(job)
                status = await settle(job)
                for page in range(0, len(requests), 100):
                    rows = await api("GET", f"/jobs/{job}/attempts?limit=100&offset={page}")
                    report["outcomes"].extend(
                        {key: r[key] for key in ("id", "trajectory_id", "state", "outcome")}
                        for r in rows
                    )
                report["last_job"] = status
                if status["state"] != "completed" or status["paused_endpoints"]:
                    break
            report["peak_observed_active"] = max(
                (s["active"] for s in report["samples"]), default=0
            )
            report["active_target_observed"] = report["peak_observed_active"] >= args.active
            report["elapsed_seconds"] = time.perf_counter() - started
            report["fake_calls"] = fake.calls if fake else None
            report["stored_after_generation"] = (await api("GET", "/trajectories?limit=1"))["total"]
            report["log_usage"] = await api("GET", "/logs/usage")
            save()
            print(
                "Workload stopped. Inspect report, actual detached failures, progress, locks and logs. Acceptance remains manual."
            )
            if args.serve:
                print("Studio retained for inspection; Ctrl+C stops it. No automatic Resume.")
                await task
    except BaseException as error:
        report["interrupted"] = type(error).__name__
        save()
        raise
    finally:
        server.should_exit = True
        await task
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-machine", action="store_true")
    parser.add_argument("--provider", choices=["fake", "local"], default="fake")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stored", type=int, default=25000)
    parser.add_argument("--active", type=int, choices=range(1, 21), default=20)
    parser.add_argument("--attempts", type=int, default=40)
    parser.add_argument("--seed", type=int, default=137)
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.stored < 1 or args.attempts < 1 or args.poll <= 0:
        parser.error("stored, attempts and poll must be positive")
    # Attached admission limit is configurable only in the app service here;
    # default studio capacity is 20 and this workload requests up to that cap.
    try:
        asyncio.run(run(args))
    except ValueError as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
