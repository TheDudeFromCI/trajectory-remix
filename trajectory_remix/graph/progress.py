"""Content-free progress while waiting for complete model responses."""

import asyncio
import json
import time


def label(value):
    return str(value).replace("\r", " ").replace("\n", " ")[:120]


def request_label(ticket):
    task = ticket.payload.get("task", {})
    event = f" event={label(task['id'])}" if task.get("id") else ""
    tier = f" tier={label(ticket.payload['tier'])}" if ticket.payload.get("tier") else ""
    size = len(json.dumps(ticket.payload.get("messages", []), ensure_ascii=False).encode())
    return (
        f"request={label(ticket.id)} trajectory={label(ticket.trajectory_id)} "
        f"operation={label(ticket.payload.get('operation', 'request'))} "
        f"endpoint={label(ticket.endpoint)}{tier}{event} prompt_bytes={size}"
    )


async def observed_request(jobs, ticket, driver, attempt):
    from copy import deepcopy

    started = time.monotonic()
    description = request_label(ticket)
    jobs.progress(ticket.job_id, f"Request started (attempt {attempt}): {description}")
    task = asyncio.create_task(driver(deepcopy(ticket)))
    key = (ticket.job_id, ticket.trajectory_id, ticket.id, ticket.endpoint)
    jobs._calls[key] = task
    try:
        # Heartbeat lines are off unless the service sets heartbeat_interval (seconds).
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=jobs.heartbeat_interval)
            if not done:
                jobs.progress(
                    ticket.job_id,
                    f"Still waiting for model response ({time.monotonic() - started:.1f}s): "
                    + description,
                )
        result = await task
        return result, time.monotonic() - started
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        jobs._calls.pop(key, None)
