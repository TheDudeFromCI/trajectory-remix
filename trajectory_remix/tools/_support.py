from __future__ import annotations
import json
import hashlib
import math
import re
from calendar import monthrange
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from ._protocol import ToolError, tool_failure
from .runtime import execution_context
from ._clock import now, anchor

STORES = ("files", "memory", "notes", "skills", "artifacts", "conversations")


class BuiltinState:
    def __init__(
        self,
        seed="2030-01-01T09:00:00+00:00",
        seconds_per_call=1,
        tick_rate=10,
        max_calls=64,
        max_bytes=2_000_000,
        *,
        random_seed=None,
    ):
        self._clock = datetime.fromisoformat(seed)
        if self._clock.tzinfo is None:
            self._clock = self._clock.replace(tzinfo=UTC)
        self.clock_tick = 0
        self.tick_rate = tick_rate
        if (
            type(seconds_per_call) not in (int, float)
            or not math.isfinite(seconds_per_call)
            or not 0 <= seconds_per_call <= 31_536_000
        ):
            raise ToolError("seconds_per_call must be finite and nonnegative")
        self.step = seconds_per_call
        self.random_seed = seed if random_seed is None else random_seed
        self.max_calls, self.max_bytes, self.calls = max_calls, max_bytes, 0
        for name in STORES:
            setattr(self, name, {})
        self.closed = False

    @property
    def clock(self):
        return now(
            {
                "clock": self._clock.isoformat(),
                "clock_timezone": getattr(self._clock.tzinfo, "key", None),
                "clock_tick": self.clock_tick,
                "tick_rate": self.tick_rate,
            },
            execution_context.get(),
        )

    @clock.setter
    def clock(self, value):
        self._clock = value
        self.clock_tick = execution_context.get().tick

    def snapshot(self):
        return {
            "clock": self._clock.isoformat(),
            "clock_timezone": getattr(self._clock.tzinfo, "key", None),
            "clock_unix": self._clock.timestamp(),
            "clock_tick": self.clock_tick,
            "tick_rate": self.tick_rate,
            "random_seed": self.random_seed,
            "calls": self.calls,
            **{name: getattr(self, name) for name in STORES},
        }

    def restore(self, state):
        """Restore a compiler-produced snapshot when extending a trajectory."""
        self._clock = datetime.fromisoformat(state["clock"])
        if state.get("clock_timezone"):
            self._clock = self._clock.astimezone(ZoneInfo(state["clock_timezone"]))
        self.clock_tick = state.get("clock_tick", 0)
        self.tick_rate = state.get("tick_rate", self.tick_rate)
        self.random_seed = state.get("random_seed", self.random_seed)
        self.calls = state.get("calls", 0)
        for name in STORES:
            setattr(self, name, state.get(name, {}))

    def path(self, path):
        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or "\\" in path
            or ".." in PurePosixPath(path).parts
        ):
            raise ToolError(
                "Virtual paths must not contain traversal, NUL or backslashes"
            )
        return "/" + str(PurePosixPath(path)).lstrip("/")

    def put(self, store, key, value):
        candidate = dict(store)
        candidate[key] = value
        total = sum(
            len(json.dumps(candidate if s is store else s).encode())
            for s in (getattr(self, name) for name in STORES)
        )
        if total > self.max_bytes:
            raise ToolError("Sample sandbox memory quota exceeded")
        store[key] = value
        return {"saved": key}

    def advance_clock(self, seconds):
        # Advance elapsed seconds across named-zone DST transitions as well.
        if isinstance(self.clock.tzinfo, ZoneInfo):
            advanced = self.clock.astimezone(UTC) + timedelta(seconds=seconds)
            self.clock = advanced.astimezone(self.clock.tzinfo)
        else:
            self.clock += timedelta(seconds=seconds)

    def set_time(self, args):
        """Choose a valid local timestamp without hidden RNG state or host time."""

        def choices(field, values):
            values = list(values)
            if not values:
                return []
            payload = json.dumps(["time-set-v1", self.random_seed, self.calls, field])
            digest = hashlib.sha256(payload.encode()).digest()
            offset = int.from_bytes(digest, "big") % len(values)
            return values[offset:] + values[:offset]

        def components(field, values):
            return choices(field, values) if args[field] is None else [args[field]]

        zones = components(
            "timezone",
            (
                "UTC",
                "America/New_York",
                "America/Los_Angeles",
                "America/Sao_Paulo",
                "Europe/London",
                "Europe/Paris",
                "Africa/Johannesburg",
                "Asia/Kolkata",
                "Asia/Tokyo",
                "Australia/Sydney",
                "Pacific/Auckland",
            ),
        )
        years = range(2000, 2100) if args["year"] is None else [args["year"]]
        months = range(1, 13) if args["month"] is None else [args["month"]]
        dates = choices(
            "date",
            (
                (year, month)
                for year in years
                for month in months
                if args["day"] is None or args["day"] <= monthrange(year, month)[1]
            ),
        )
        hours = components("hour", range(24))
        minutes = components("minute", range(60))
        second = components("second", range(60))[0]
        for zone_name in zones:
            try:
                if re.fullmatch(r"(?:UTC)?[+-](?:[01]\d|2[0-3]):[0-5]\d", zone_name):
                    offset = zone_name.removeprefix("UTC")
                    seconds = (int(offset[1:3]) * 60 + int(offset[4:])) * 60
                    zone = timezone(
                        timedelta(seconds=seconds if offset[0] == "+" else -seconds)
                    )
                else:
                    zone = ZoneInfo(zone_name)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ToolError(
                    "Invalid timezone; use an IANA name, UTC or ±HH:MM"
                ) from error
            for year, month in dates:
                for day in components("day", range(1, monthrange(year, month)[1] + 1)):
                    for hour in hours:
                        for minute in minutes:
                            candidate = datetime(
                                year, month, day, hour, minute, second, tzinfo=zone
                            )
                            # Reject local times skipped by timezone transitions;
                            # ambiguous times use the first occurrence (fold=0).
                            try:
                                valid = (
                                    candidate.astimezone(UTC).astimezone(zone)
                                    == candidate
                                )
                            except (ValueError, OverflowError):
                                valid = False
                            if valid:
                                self.clock = candidate
                                return {
                                    "timestamp": self.clock.isoformat(),
                                    "synthetic": True,
                                }
        raise ToolError(
            "Invalid date or nonexistent local time for the supplied fields"
        )

    @staticmethod
    def artifact_key(path, version):
        return json.dumps([path, version])

    def file_version(self, path):
        return "sha256:" + hashlib.sha256(self.files[path].encode()).hexdigest()

    def write_file(self, path, content, expected=None):
        if expected is not None and (
            path not in self.files or self.file_version(path) != expected
        ):
            raise ToolError("File version conflict")
        version = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        files = {**self.files, path: content}
        artifacts = {**self.artifacts, self.artifact_key(path, version): content}
        replacements = {"files": files, "artifacts": artifacts}
        if (
            sum(
                len(json.dumps(replacements.get(name, getattr(self, name))).encode())
                for name in STORES
            )
            > self.max_bytes
        ):
            raise ToolError("Sample sandbox memory quota exceeded")
        self.files, self.artifacts = files, artifacts
        return {"saved": path, "version": version}

    def conversation_summary(self, cid):
        conversation = self.conversations[cid]
        return {
            "conversationId": cid,
            "status": conversation["status"],
            "turns": sum(m["role"] == "assistant" for m in conversation["messages"]),
        }

    def begin_subagent(self, args, conversation_id=None):
        """Begin an authored turn using only context available at dispatch."""
        cid = (
            args.get("resume")
            or conversation_id
            or f"conversation-{len(self.conversations) + 1}"
        )
        if "resume" not in args and conversation_id is None:
            number = len(self.conversations) + 1
            while cid in self.conversations:
                number += 1
                cid = f"conversation-{number}"
        if not isinstance(cid, str) or not cid or len(cid) > 128:
            raise ToolError("Invalid conversationId")
        if "resume" in args:
            if cid not in self.conversations:
                raise ToolError("Unknown conversationId")
            if conversation_id is not None and conversation_id != cid:
                raise ToolError("Recorded conversationId does not match resume")
            if self.conversations[cid]["status"] in ("running", "closed"):
                raise ToolError("Conversation is running or closed")
            conversation = deepcopy(self.conversations[cid])
        else:
            if cid in self.conversations:
                raise ToolError("conversationId already exists; use resume")
            conversation = {"status": "running", "messages": []}
        content = args["task"]
        if args.get("artifacts"):
            artifacts = []
            for ref in args["artifacts"]:
                path = self.path(ref["path"])
                key = self.artifact_key(path, ref["version"])
                if key not in self.artifacts:
                    raise ToolError("Artifact version not found")
                artifacts.append({**ref, "path": path, "content": self.artifacts[key]})
            content += "\n\nReferenced artifacts (data):\n" + json.dumps(
                artifacts, ensure_ascii=False
            )
        conversation["messages"].append({"role": "user", "content": content})
        conversation["status"] = "running"
        self.put(self.conversations, cid, conversation)
        return cid

    def finish_subagent(self, cid, outcome):
        conversation = deepcopy(self.conversations[cid])
        if not outcome["ok"]:
            conversation["status"] = "failed"
            self.put(self.conversations, cid, conversation)
            return None
        response = outcome["result"]["response"]
        if (
            not isinstance(response, str)
            or not response.strip()
            or len(response) > 200_000
        ):
            raise ToolError(
                "Subagent must return nonempty text of at most 200,000 characters"
            )
        conversation["messages"].append({"role": "assistant", "content": response})
        conversation["status"] = "ready"
        self.put(self.conversations, cid, conversation)
        return {"conversationId": cid, "response": response}


def run_builtin(args, state, windows, operation, *, modifies=False):
    """Adapt the built-in simulation helpers to the public tick protocol."""
    context = execution_context.get()
    if context.elapsed < context.duration:
        return "", False, False
    runtime = load_state(state)
    modified = modifies or not context.invisible or "clock" not in state
    try:
        if not context.invisible:
            runtime.calls += 1
        result = operation(runtime, args, windows)
        output = {"ok": True, "result": result}
    except (ToolError, ValueError, OverflowError) as error:
        output = tool_failure(error)
    finally:
        if modified:
            state.update(runtime.snapshot())
    return json.dumps(output, ensure_ascii=False), True, modified


def load_state(state):
    context = execution_context.get()
    runtime = BuiltinState(
        **context.clock, random_seed=context.seed, max_bytes=context.max_bytes
    )
    runtime.tick_rate = state.get("tick_rate", runtime.tick_rate)
    if "clock" in state:
        runtime.restore(state)
    elif "clock_unix" in state:
        value, _ = anchor(state, context.clock)
        runtime.restore({**state, "clock": value.isoformat()})
    return runtime
