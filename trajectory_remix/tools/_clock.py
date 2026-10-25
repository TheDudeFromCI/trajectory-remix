"""Tool-owned synthetic time: a Unix/tick anchor plus tokens per second."""

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from ._protocol import ToolError

DEFAULT_SEED = "2030-01-01T09:00:00+00:00"


def initialize(state, options):
    if not isinstance(options, dict) or set(options) - {
        "seed",
        "tick_rate",
        "seconds_per_call",
    }:
        raise ToolError("synthetic_clock accepts seed and tick_rate")
    datetime.fromisoformat(options.get("seed", DEFAULT_SEED))
    rate = options.get("tick_rate", 10)
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ToolError("tick_rate must be finite and positive")
    state.setdefault("tick_rate", rate)


def anchor(state, options):
    value = (
        datetime.fromtimestamp(state["clock_unix"], UTC)
        if "clock_unix" in state and "clock" not in state
        else datetime.fromisoformat(
            state.get("clock", options.get("seed", DEFAULT_SEED))
        )
    )
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if state.get("clock_timezone"):
        value = value.astimezone(ZoneInfo(state["clock_timezone"]))
    return value, state.get("clock_tick", 0)


def now(state, context):
    base, tick = anchor(state, context.clock)
    seconds = (context.tick - tick) / state.get(
        "tick_rate", context.clock.get("tick_rate", 10)
    )
    return (base.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(base.tzinfo)


def unix_time(state, context):
    return now(state, context).timestamp()


def elapsed_seconds(started_iso, state, context):
    # Datetime subtraction avoids cancellation of large Unix timestamps, and UTC
    # avoids treating DST transitions as changes to elapsed wall seconds.
    started = datetime.fromisoformat(started_iso).astimezone(UTC)
    return (now(state, context).astimezone(UTC) - started).total_seconds()
