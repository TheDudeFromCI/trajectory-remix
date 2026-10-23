"""Attach an actual external specialist to the MCP coordination benchmark."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp_call import call, connect


async def run(args):
    state = json.loads((args.work / "state.json").read_text())
    job = state[args.stage][args.role]
    common = {
        "session_id": state["session_id"],
        "job_id": job["jobId"],
        "worker_id": args.worker_id,
    }
    async with connect(args.url) as client:
        if args.action == "claim":
            result = await call(client, "claim_subagent_job", common)
        elif args.action == "complete":
            response = args.input.read_text() if args.input else sys.stdin.read()
            result = await call(
                client, "complete_subagent_job", {**common, "response": response}
            )
        else:
            requests = json.loads(
                args.input.read_text() if args.input else sys.stdin.read()
            )
            multiple = isinstance(requests, list)
            results = [
                await call(client, "call_subagent_tool", {**common, **r})
                for r in (requests if multiple else [requests])
            ]
            result = results if multiple else results[0]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["claim", "tool", "complete"])
    parser.add_argument("--role", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument(
        "--stage", choices=["initial", "followups", "final_reviews"], default="initial"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--work", type=Path, default=Path("work/coordination-benchmark")
    )
    parser.add_argument("--input", type=Path)
    asyncio.run(run(parser.parse_args()))
