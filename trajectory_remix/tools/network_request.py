"""Simulated HTTP requests against author-provided fixtures."""

import copy
import json
from fnmatch import fnmatchcase
from urllib.parse import urlsplit
from .network_set import HEADERS, METHODS
from .runtime import execution_context
from ._clock import now, elapsed_seconds
from ._simulation import result, failure
from ._access import io_failure

name = "network_request"
description = "Make a simulated HTTP request. url, optional method (GET), headers and body. timeout_seconds defaults to 30 simulated seconds. Returns status, headers and body, including HTTP errors like 404/500. Transport failures are tool errors. Uses authored fixtures; never contacts the internet or follows redirects."
schema = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "minLength": 1, "maxLength": 2048},
        "method": {"type": "string", "enum": METHODS},
        "headers": HEADERS,
        "body": {"type": "string", "maxLength": 100000},
        "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 86400},
    },
    "required": ["url"],
    "additionalProperties": False,
}


def cleanup(state, call_id):
    pending = state.get("network_pending", {})
    modified = call_id in pending
    pending.pop(call_id, None)
    if not pending:
        state.pop("network_pending", None)
    return modified


def run(args, state, windows):
    context = execution_context.get()
    if context.cancelled:
        return "", True, cleanup(state, context.call_id)
    if context.elapsed == 0:
        failed = io_failure(state, name, args)
        if failed is not None:
            return failed
    if context.elapsed < context.duration:
        return "", False, False
    pending = state.get("network_pending", {})
    entry = pending.get(context.call_id)
    modified = False
    if entry is None:
        try:
            url = urlsplit(args["url"])
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError(
                    "URL must use http/https, with a host and no embedded credentials"
                )
            url.port  # Validate port syntax.
        except ValueError as error:
            return failure(error)
        method = args.get("method", "GET")
        headers = {key.lower(): value for key, value in args.get("headers", {}).items()}
        config = state.get("network", {})
        match = next(
            (
                (index, route)
                for index, route in enumerate(config.get("routes", []))
                if method == route.get("method", "GET")
                and fnmatchcase(args["url"], route["url"])
                and (
                    "request_body" not in route
                    or route["request_body"] == args.get("body", "")
                )
                and all(
                    headers.get(key.lower()) == value
                    for key, value in route.get("request_headers", {}).items()
                )
            ),
            None,
        )
        if match is None:
            return failure(f"No simulated network route: {method} {args['url']}")
        index, route = match
        key = str(index)
        cursor = config.get("cursors", {}).get(key, 0)
        response = copy.deepcopy(
            route["responses"][min(cursor, len(route["responses"]) - 1)]
        )
        entry = {"response": response, "started_iso": now(state, context).isoformat()}
        candidate = {**pending, context.call_id: entry}
        cursors = {
            **config.get("cursors", {}),
            key: min(cursor + 1, len(route["responses"]) - 1),
        }
        if (
            len(json.dumps([{**config, "cursors": cursors}, candidate]).encode())
            > context.max_bytes
        ):
            return failure("Network pending-request quota exceeded")
        config["cursors"] = cursors
        state.setdefault("network_pending", {})[context.call_id] = entry
        modified = True
    elapsed = max(0, elapsed_seconds(entry["started_iso"], state, context))
    response = entry["response"]
    latency = response.get("latency_seconds", 0)
    timeout = args.get("timeout_seconds", 30)
    # At a tie, an available response wins; an explicit fixture timeout always fails.
    timed_out = (
        latency > timeout or response.get("error") == "timeout"
    ) and elapsed + 1e-6 >= timeout
    ready = elapsed + 1e-6 >= latency and response.get("error") != "timeout"
    if not ready and not timed_out:
        return "", False, modified
    cleanup(state, context.call_id)
    if timed_out:
        return failure("ETIMEDOUT: Simulated network request timed out", modified=True)
    if "error" in response:
        return failure(f"Network transport error: {response['error']}", modified=True)
    return result(
        {
            "url": args["url"],
            "method": args.get("method", "GET"),
            "status": response.get("status", 200),
            "headers": response.get("headers", {}),
            "body": "" if args.get("method") == "HEAD" else response.get("body", ""),
            "elapsed_seconds": round(elapsed, 6),
        },
        modified=True,
    )
