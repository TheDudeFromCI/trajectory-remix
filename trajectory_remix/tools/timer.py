"""Logical tick timers and alarms, without host sleeping or background jobs."""

from datetime import datetime
from .runtime import execution_context
from ._support import load_state
from ._simulation import result, failure

name = "timer"
description = "Wait using logical ticks. timeout: ticks (>=0); interval: ticks (>0) and count (finite number of firings); alarm: at_tick or timezone-aware ISO timestamp from the synthetic clock. Timestamp alarms require authored clock advancement. timeout_ticks optionally limits alarm waiting (default 1000)."
schema = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["timeout", "interval", "alarm"]},
        "ticks": {"type": "integer", "minimum": 0, "maximum": 99999},
        "count": {"type": "integer", "minimum": 1, "maximum": 1024},
        "at_tick": {"type": "integer", "minimum": 0, "maximum": 99999},
        "timestamp": {"type": "string", "minLength": 1, "maxLength": 128},
        "timeout_ticks": {"type": "integer", "minimum": 0, "maximum": 99999},
    },
    "required": ["mode"],
    "additionalProperties": False,
    "oneOf": [
        {"properties": {"mode": {"const": "timeout"}}, "required": ["ticks"]},
        {
            "properties": {"mode": {"const": "interval"}, "ticks": {"minimum": 1}},
            "required": ["ticks", "count"],
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
