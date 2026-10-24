"""Logical tick timers and alarms, without host sleeping or background jobs."""

from datetime import datetime
from .runtime import execution_context
from ._support import load_state
from ._simulation import result, failure
from ._clock import unix_time, now, elapsed_seconds

name = "timer"
description = "Wait in simulated seconds at the trajectory tick_rate. timeout: seconds >=0; interval: seconds >0 and finite count. alarm: timezone-aware timestamp or at_tick; timeout_seconds defaults to 100. Explicit ticks/timeout_ticks are supported as alternatives. No host sleeping."
schema = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["timeout", "interval", "alarm"]},
        "ticks": {"type": "integer", "minimum": 0, "maximum": 99999},
        "seconds": {"type": "number", "minimum": 0},
        "timeout_seconds": {"type": "number", "minimum": 0},
        "count": {"type": "integer", "minimum": 1, "maximum": 1024},
        "at_tick": {"type": "integer", "minimum": 0, "maximum": 99999},
        "timestamp": {"type": "string", "minLength": 1, "maxLength": 128},
        "timeout_ticks": {"type": "integer", "minimum": 0, "maximum": 99999},
    },
    "required": ["mode"],
    "additionalProperties": False,
    "oneOf": [
        {
            "properties": {"mode": {"const": "timeout"}},
            "oneOf": [
                {"required": ["ticks"], "not": {"required": ["seconds"]}},
                {"required": ["seconds"], "not": {"required": ["ticks"]}},
            ],
        },
        {
            "properties": {
                "mode": {"const": "interval"},
                "ticks": {"minimum": 1},
                "seconds": {"exclusiveMinimum": 0},
            },
            "required": ["count"],
            "oneOf": [
                {"required": ["ticks"], "not": {"required": ["seconds"]}},
                {"required": ["seconds"], "not": {"required": ["ticks"]}},
            ],
        },
        {
            "properties": {"mode": {"const": "alarm"}},
            "oneOf": [
                {"required": ["at_tick"], "not": {"required": ["timestamp"]}},
                {"required": ["timestamp"], "not": {"required": ["at_tick"]}},
            ],
        },
    ],
}


def run(args, state, windows):
    context = execution_context.get()
    elapsed = context.elapsed - context.duration
    if elapsed < 0:
        return "", False, False
    mode = args["mode"]
    if "seconds" in args or mode == "alarm" and "timeout_ticks" not in args:
        return run_seconds(args, state, context)
    if mode == "timeout":
        if elapsed < args["ticks"]:
            return "", False, False
        return result({"mode": mode, "tick": context.tick})
    if mode == "interval":
        if elapsed == 0 or elapsed % args["ticks"]:
            return "", False, False
        firing = elapsed // args["ticks"]
        return result(
            {"mode": mode, "tick": context.tick, "firing": firing},
            done=firing >= args["count"],
        )
    try:
        if "at_tick" in args:
            reached = context.tick >= args["at_tick"]
        else:
            target = datetime.fromisoformat(args["timestamp"])
            if target.tzinfo is None:
                raise ValueError("Alarm timestamp must include a timezone")
            reached = load_state(state).clock >= target
        if reached:
            return result({"mode": mode, "tick": context.tick})
        if elapsed >= args.get("timeout_ticks", 1000):
            raise ValueError("Alarm timed out before its target was reached")
        return "", False, False
    except (ValueError, OverflowError) as error:
        return failure(error)


def run_seconds(args, state, context):
    mode = args["mode"]
    current = unix_time(state, context)
    timers = state.get("timers", {})
    entry = timers.get(context.call_id)
    initialized = entry is None
    if initialized:
        entry = {
            "started_unix": current,
            "started_iso": now(state, context).isoformat(),
            "fired": 0,
        }
        timers = state.setdefault("timers", {})
        timers[context.call_id] = entry
    elapsed = max(0, elapsed_seconds(entry["started_iso"], state, context))
    done = False
    modified = initialized
    try:
        if mode == "alarm":
            if "at_tick" in args:
                ready = context.tick >= args["at_tick"]
            else:
                target = datetime.fromisoformat(args["timestamp"])
                if target.tzinfo is None:
                    raise ValueError("Alarm timestamp must include a timezone")
                ready = current >= target.timestamp()
            if not ready and elapsed + 1e-6 >= args.get("timeout_seconds", 100):
                raise ValueError("Alarm timed out before its target was reached")
            done = ready
        else:
            target = args["seconds"] * (entry["fired"] + 1 if mode == "interval" else 1)
            ready = elapsed + 1e-6 >= target
            if ready:
                entry["fired"] += 1
                modified = True
                done = mode == "timeout" or entry["fired"] >= args["count"]
        if not ready:
            return "", False, modified
        value = {
            "mode": mode,
            "tick": context.tick,
            "elapsed_seconds": round(elapsed, 6),
        }
        if mode == "interval":
            value["firing"] = entry["fired"]
        return result(value, done=done, modified=True)
    except (ValueError, OverflowError) as error:
        done = True
        return failure(error, modified=True)
    finally:
        if done:
            timers.pop(context.call_id, None)
            if not timers:
                state.pop("timers", None)
