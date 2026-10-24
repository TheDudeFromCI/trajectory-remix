"""Poll a virtual file for completed changes while the call remains active."""

import hashlib
from .runtime import execution_context
from ._support import load_state
from ._simulation import result, failure
from ._clock import unix_time, now, elapsed_seconds
from ._access import io_failure
from ._cancellation import remove_pending

name = "file_watch"
description = "Watch a virtual path for created, modified, or deleted content. Emits matching changes and finishes after count matches (default 1), or timeout_seconds (default 100 simulated seconds; timeout_ticks is an explicit alternative). Observes completed file changes once per logical tick, not host files."
schema = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "events": {
            "type": "array",
            "items": {"type": "string", "enum": ["created", "modified", "deleted"]},
            "minItems": 1,
            "maxItems": 3,
            "uniqueItems": True,
        },
        "count": {"type": "integer", "minimum": 1, "maximum": 1024},
        "timeout_ticks": {"type": "integer", "minimum": 0, "maximum": 99999},
        "timeout_seconds": {"type": "number", "minimum": 0},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def run(args, state, windows):
    context = execution_context.get()
    if context.cancelled:
        return remove_pending(state, "file_watchers", context.call_id)
    if context.elapsed == 0:
        failed = io_failure(state, name, args)
        if failed is not None:
            return failed
    if context.elapsed < context.duration:
        return "", False, False
    try:
        path = load_state(state).path(args["path"])
    except ValueError as error:
        return failure(error)
    content = state.get("files", {}).get(path)
    version = (
        "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        if content is not None
        else None
    )
    watchers = state.get("file_watchers", {})
    entry = watchers.get(context.call_id)
    modified = False
    if entry is None:
        entry = {
            "version": version,
            "matches": 0,
            "started_unix": unix_time(state, context),
            "started_iso": now(state, context).isoformat(),
        }
        state.setdefault("file_watchers", {})[context.call_id] = entry
        watchers = state["file_watchers"]
        modified = True
    before = entry["version"]
    change = None
    if before != version:
        change = (
            "created"
            if before is None
            else "deleted"
            if version is None
            else "modified"
        )
        entry["version"] = version
        modified = True
    value = None
    done = False
    if change in args.get("events", ["created", "modified", "deleted"]):
        entry["matches"] += 1
        value = {
            "path": path,
            "event": change,
            "version": version,
            "tick": context.tick,
        }
        done = entry["matches"] >= args.get("count", 1)
    if "timeout_ticks" in args:
        expired = context.elapsed - context.duration >= args["timeout_ticks"]
    else:
        expired = elapsed_seconds(
            entry["started_iso"], state, context
        ) + 1e-6 >= args.get("timeout_seconds", 100)
    timed_out = not done and expired
    if done or timed_out:
        del watchers[context.call_id]
        if not watchers:
            state.pop("file_watchers", None)
        modified = True
    if timed_out:
        return failure(ValueError(f"File watch timed out: {path}"), modified=True)
    if value is not None:
        return result(value, done=done, modified=modified)
    return "", False, modified
