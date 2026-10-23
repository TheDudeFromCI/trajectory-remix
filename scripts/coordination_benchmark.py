"""Exercise the original Kidra coordination prompt through a real MCP connection.

launch -> attach six actual external workers -> followups -> resume the same workers
-> finish. Specialist responses and implementation files are never authored here.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from mcp_call import call, connect

ORIGINAL_PROMPT = (
    "Let's make it a software task. The user asks Kidra to write some code, then Kidra breaks down the problem "
    "and spins off half a dozen subagents to write the code, test, write docs, and validate each other's work. "
    "All the while, she should be informing the user of the runtimes status. Kidra should use the `notes` tool "
    "for breaking the project into multiple TODOs. She should also ask the subagents for clarifying questions "
    "about the code they wrote. Kidra is a coordinator, not a coder."
)
REQUIREMENTS = """Build a Python standard-library-only slug generator.
slugify.py exports slugify(text, max_length=48). Require text to be str (otherwise TypeError).
Require max_length to be a positive integer in 1..128; bool is invalid (ValueError).
Use Unicode NFKD normalization, discard non-ASCII characters, lowercase, replace runs of
non-alphanumeric ASCII characters with one hyphen, and strip leading/trailing hyphens.
Truncate to max_length characters and strip any trailing hyphen. If the result is empty,
use 'untitled' truncated to max_length. Output is always nonempty and at most max_length.
slug_cli.py accepts one positional text argument and --max-length (default 48), prints
the slug plus newline, returns zero on success, and handles invalid input with a nonzero
exit code and no traceback. --help must work.
Provide unittest tests covering Unicode, punctuation, fallback, truncation, invalid types,
boundaries, and at least five CLI cases. Write README.md documenting API and CLI behavior.
Two independent reviewers must inspect actual implementation, tests and docs, run checks,
and report limitations truthfully. Kidra delegates implementation and asks follow-ups.
"""
TASKS = {
    "core": (
        "implementation",
        "Implement only /slugify.py according to /requirements.md. Publish it using file_write. Explain one key design choice. Do not write the CLI, docs or tests.",
    ),
    "cli": (
        "implementation",
        "Implement only /slug_cli.py according to /requirements.md, importing slugify from slugify.py. Publish it using file_write. Explain argument/error handling. Other specialists own the core and tests.",
    ),
    "tests": (
        "testing",
        "Write /test_slug.py using unittest from /requirements.md. Cover at least 12 cases including five CLI cases via subprocess using sys.executable. Core/CLI will be shared on follow-up; do not claim execution before they exist.",
    ),
    "docs": (
        "documentation",
        "Write /README.md from /requirements.md with API and CLI examples and limits. Implementation arrives on follow-up; label any unverified examples honestly.",
    ),
    "api_review": (
        "review",
        "Create /review_api.md with an API review checklist from /requirements.md. Actual source arrives on follow-up; do not claim a completed source review yet.",
    ),
    "integration_review": (
        "review",
        "Create /review_integration.md with an integration/docs/CLI review checklist from /requirements.md. Actual source, tests and docs arrive on follow-up; do not claim they were inspected yet.",
    ),
}
FOLLOWUPS = {
    "core": "Clarify how your implementation handles bool max_length, Unicode that vanishes under ASCII normalization, and truncation at a separator. Inspect the supplied tests. If a defect is real, fix only your own file and explain it; otherwise explain why it satisfies the contract.",
    "cli": "Clarify invalid --max-length handling, exit status and traceback behavior. Inspect the supplied core/tests/docs. If a defect is real, fix only your own file; otherwise explain the error behavior.",
    "tests": "Run your tests against the supplied implementation using python_run. Inspect real stdout/stderr and exit status. Fix tests only if they contradict requirements, then rerun. Do not fix other owners' implementation; report defects with the executionId. Explain which edge cases your tests protect.",
    "docs": "Compare README.md with the supplied implementation and tests. Run documented examples using python_run against actual files; update only README.md if needed. Explain any documentation assumptions and cite the real executionId.",
    "api_review": "Review the supplied implementation, tests and README against requirements. Independently execute edge-case checks using python_run (you may add /test_review.py for missing cases). Update only review_api.md and your own extra tests. Explain findings and cite actual executionIds; do not modify implementation.",
    "integration_review": "Independently review the supplied core, CLI, tests and README together. Run unittest and CLI checks using python_run. Update only review_integration.md with context, actual evidence IDs, findings and limitations. Explain why documented examples match actual behavior; do not modify implementation.",
}


async def run(args):
    state_path = args.work / "state.json"
    args.work.mkdir(parents=True, exist_ok=True)
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"sample_id": args.sample_id, "session_id": "benchmark"}
    )
    async with connect(args.url) as client:

        async def invoke(operation, **arguments):
            return await call(client, operation, arguments)

        async def root(name, arguments):
            result = await invoke(
                "call_simulated_tool",
                session_id=state["session_id"],
                name=name,
                arguments=arguments,
            )
            observation = result["interaction"]["outcome"]
            if not observation["ok"]:
                raise RuntimeError(json.dumps(observation))
            return observation["result"]

        async def progress(prefix):
            status = await root("runtime_status", {})
            counts = {
                s: sum(j["status"] == s for j in status["jobs"])
                for s in ("queued", "running", "completed", "failed", "cancelled")
            }
            await root(
                "chat",
                {
                    "content": f"{prefix} Runtime jobs: "
                    + ", ".join(f"{k}={v}" for k, v in counts.items())
                    + "."
                },
            )
            return counts

        if args.stage == "launch":
            await invoke("create_trajectory", sample_id=state["sample_id"])
            await invoke(
                "create_event",
                sample_id=state["sample_id"],
                tick="0",
                event={"op": "open", "window": "chat", "window-type": "chat"},
            )
            await invoke(
                "create_event",
                sample_id=state["sample_id"],
                tick="0",
                event={
                    "op": "append",
                    "window": "chat",
                    "source": "user",
                    "content": "User: Build a slug generator, CLI, tests and documentation. Delegate to six specialists and coordinate their reviews.\n",
                },
            )
            await invoke(
                "create_tool_call",
                sample_id=state["sample_id"],
                tick="0",
                tool="file_write",
                arguments={"path": "/requirements.md", "content": REQUIREMENTS},
                invisible=True,
                source="user",
            )
            await invoke(
                "update_trajectory_metadata",
                sample_id=state["sample_id"],
                patch={
                    "metadata": {
                        "agent_name": "Kidra",
                        "tags": ["coordination-benchmark", "live-external-workers"],
                        "benchmark_prompt": ORIGINAL_PROMPT,
                        "coordination_contract": {
                            "minimum_subagents": 6,
                            "coordinator_only": True,
                            "require_followups": True,
                            "require_execution": True,
                            "require_reviews": True,
                        },
                    }
                },
            )
            await invoke(
                "create_tool_session",
                session_id=state["session_id"],
                external_agents=True,
                enable_execution=True,
                trajectory_id=state["sample_id"],
                max_calls=256,
            )
            ref = {
                "path": "/requirements.md",
                "version": "sha256:"
                + hashlib.sha256(REQUIREMENTS.encode()).hexdigest(),
            }
            state["initial"] = {}
            for name, (role, task) in TASKS.items():
                await root(
                    "note_save",
                    {
                        "key": name,
                        "text": f"{name}: delegated work and clarification",
                        "status": "doing",
                        "owner": name,
                        "acceptance_criteria": [
                            "Deliver scoped artifacts",
                            "Answer a clarifying follow-up",
                        ],
                        "requires_evidence": role in ("testing", "review"),
                    },
                )
                state["initial"][name] = await root(
                    "subagent",
                    {
                        "task": task,
                        "role": role,
                        "background": True,
                        "artifacts": [ref],
                    },
                )
            await progress(
                "I have split the project into six TODOs and launched all six specialists."
            )
        elif args.stage == "followups":
            workspace = await invoke(
                "get_tool_session", session_id=state["session_id"], include_log=False
            )
            if any(
                workspace["state"]["jobs"][job["jobId"]]["status"] != "completed"
                for job in state["initial"].values()
            ):
                raise RuntimeError("Initial specialists have not all completed")
            refs = [
                {
                    "path": p,
                    "version": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                }
                for p, text in sorted(workspace["state"]["files"].items())
            ]
            state["followups"] = {}
            for name, (role, _) in TASKS.items():
                state["followups"][name] = await root(
                    "subagent",
                    {
                        "task": FOLLOWUPS[name],
                        "role": role,
                        "background": True,
                        "resume": state["initial"][name]["conversationId"],
                        "artifacts": refs,
                    },
                )
            await progress(
                "Initial artifacts are available. I have resumed the same six conversations for clarifications, tests and cross-review."
            )
        elif args.stage == "status":
            print(json.dumps(await progress("Coordination progress.")))
        elif args.stage == "refresh-reviews":
            workspace = await invoke(
                "get_tool_session", session_id=state["session_id"], include_log=False
            )
            refs = [
                {
                    "path": p,
                    "version": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                }
                for p, text in sorted(workspace["state"]["files"].items())
                if not p.startswith("/review_")
            ]
            state["final_reviews"] = {}
            for name in ("api_review", "integration_review"):
                state["final_reviews"][name] = await root(
                    "subagent",
                    {
                        "task": (
                            "The documentation specialist has now verified examples and updated README.md. "
                            "Inspect these final pinned source/tests/docs versions, confirm whether your earlier "
                            "README findings are resolved, run fresh unittest discovery including test_review.py, "
                            "and update only your own review report to reflect final artifacts and real evidence."
                        ),
                        "role": "review",
                        "background": True,
                        "resume": state["initial"][name]["conversationId"],
                        "artifacts": refs,
                    },
                )
            await progress(
                "Final documentation is available. I have asked the same reviewers to verify the updated handoff."
            )
        elif args.stage == "finish":
            args.output.mkdir(parents=True, exist_ok=True)
            workspace = await invoke(
                "get_tool_session", session_id=state["session_id"], include_log=False
            )
            jobs = workspace["state"]["jobs"]
            for handles in (
                state["initial"],
                state["followups"],
                state.get("final_reviews", {}),
            ):
                if any(
                    jobs[j["jobId"]]["status"] != "completed" for j in handles.values()
                ):
                    raise RuntimeError("Some benchmark jobs are not complete")
                for handle in handles.values():
                    await root("subagent_result", {"jobId": handle["jobId"]})
            execution = await root(
                "python_run",
                {"argv": ["-m", "unittest", "discover", "-v"], "timeout_seconds": 20},
            )
            if execution["status"] != "passed":
                raise RuntimeError(json.dumps(execution))
            count = re.search(r"Ran (\d+) tests?", execution["stderr"])
            if not count or int(count[1]) < 12:
                raise RuntimeError("Benchmark must execute at least 12 unittest cases")
            for name, (role, _) in TASKS.items():
                await root(
                    "note_save",
                    {
                        "key": name,
                        "text": f"{name}: artifacts, follow-up and verification complete",
                        "status": "done",
                        "evidence": (
                            [execution["executionId"]]
                            if role in ("testing", "review")
                            else []
                        ),
                    },
                )
            await progress(
                f"All six specialists completed their work and follow-ups. Fresh isolated verification passed {count[1]} tests."
            )
            await invoke("finalize_trajectory", sample_id=state["sample_id"])
            report = await invoke("validate_trajectory", sample_id=state["sample_id"])
            if not report["valid"] or report["diagnostics"]:
                raise RuntimeError(json.dumps(report))
            await invoke("save_dataset")
            source = (await invoke("get_trajectory", sample_id=state["sample_id"]))[
                "sample"
            ]
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "coordination-trajectory.json").write_text(
                json.dumps(source, indent=2) + "\n"
            )
            report.update(
                benchmark_mode="six live external workers", final_execution=execution
            )
            (args.output / "coordination-validation.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            if args.dataset:
                with (
                    sqlite3.connect(args.dataset) as original,
                    sqlite3.connect(
                        args.output / "coordination-benchmark.sqlite"
                    ) as backup,
                ):
                    original.backup(backup)
            state["report"] = report
            print(json.dumps(report))
        elif args.stage == "export":
            report = await invoke("validate_trajectory", sample_id=state["sample_id"])
            source = (await invoke("get_trajectory", sample_id=state["sample_id"]))[
                "sample"
            ]
            if not report["valid"] or report["diagnostics"]:
                raise RuntimeError(json.dumps(report))
            execution = [
                event["outcome"]["result"]
                for events in source["replay"].values()
                for event in events
                if event.get("op") == "tool-call" and event.get("tool") == "python_run"
            ][-1]
            report.update(
                benchmark_mode="six live external workers", final_execution=execution
            )
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "coordination-trajectory.json").write_text(
                json.dumps(source, indent=2) + "\n"
            )
            (args.output / "coordination-validation.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            if args.dataset:
                with (
                    sqlite3.connect(args.dataset) as original,
                    sqlite3.connect(
                        args.output / "coordination-benchmark.sqlite"
                    ) as backup,
                ):
                    original.backup(backup)
            state["report"] = report
            print(json.dumps(report))
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        if args.stage in ("launch", "followups", "refresh-reviews"):
            print(
                json.dumps(
                    state[
                        {
                            "launch": "initial",
                            "followups": "followups",
                            "refresh-reviews": "final_reviews",
                        }[args.stage]
                    ],
                    indent=2,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "launch",
            "followups",
            "refresh-reviews",
            "status",
            "finish",
            "export",
        ],
    )
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--work", type=Path, default=Path("work/coordination-benchmark")
    )
    parser.add_argument("--sample-id", default="kidra-six-specialists")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/coordination-output"))
    asyncio.run(run(parser.parse_args()))
