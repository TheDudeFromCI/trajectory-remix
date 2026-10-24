"""Capability sandbox: all tool state is JSON data in RAM, isolated per sample.

There is deliberately no code execution, shell, host file access, network access,
or real subagent delegation. Subagent replies are authored synthetic data.
"""

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


class ToolError(ValueError):

    def __init__(self, message, conversation_id=None):
        super().__init__(message)
        self.conversation_id = conversation_id


def tool_failure(error):
    outcome = {"ok": False, "error": str(error)}
    cid = getattr(error, "conversation_id", None)
    if cid is not None:
        outcome["conversationId"] = cid
    return outcome


def validate_subagent_outcome(outcome):
    """Validate an author-supplied reply, never a request to a model provider."""
    if not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool:
        raise ToolError("Recorded outcome requires an ok boolean")
    result = outcome.get("result")
    if outcome["ok"]:
        if not isinstance(result, dict) or not isinstance(result.get("response"), str):
            raise ToolError("Subagent outcome requires result.response text")
        if not result["response"].strip() or len(result["response"]) > 200_000:
            raise ToolError(
                "Subagent must return nonempty text of at most 200,000 characters"
            )
    elif not isinstance(outcome.get("error"), str):
        raise ToolError("Failed outcome requires error text")
    for container in (outcome, result if isinstance(result, dict) else {}):
        if "conversationId" in container and (
            not isinstance(container["conversationId"], str)
            or not container["conversationId"]
            or len(container["conversationId"]) > 128
        ):
            raise ToolError("Invalid recorded conversationId")
    if len(json.dumps(outcome).encode()) > 200_000:
        raise ToolError("Recorded outcome exceeds 200,000 bytes")


def tool_text(text, special_tokens, *, json_encoded=False):
    """Keep tool data from becoming model-owned control tokens."""
    escapes = {}
    for spelling in special_tokens:
        units = spelling.encode("utf-16-be")
        key = (
            json.dumps(spelling, ensure_ascii=False)[1:-1] if json_encoded else spelling
        )
        escapes[key] = "".join(
            f"\\u{int.from_bytes(units[i:i + 2], 'big'):04x}"
            for i in range(0, len(units), 2)
        )
    pattern = "|".join(re.escape(s) for s in sorted(escapes, key=len, reverse=True))
    return re.sub(pattern, lambda m: escapes[m[0]], text) if pattern else text


def tool_json(value, special_tokens):
    """Serialize observations/calls without embedding model control tokens."""
    return tool_text(json.dumps(value, ensure_ascii=False), special_tokens, json_encoded=True)


def chat_context(windows, metadata):
    """Per-call trajectory context; standalone sandboxes have no chat window."""
    return {
        "chat_open": any(w["id"] == "chat" and w["close"] is None for w in windows),
        "agent_name": (
            metadata.get("agent_name", "Kidra") if isinstance(metadata, dict) else "Kidra"
        ),
    }


def tool_message(tag, value, special_tokens):
    """Keep JSON data from introducing protocol tags or model control tokens."""
    payload = tool_json(value, special_tokens).replace("<", "\\u003c")
    return f"<{tag}>{payload}</{tag}>"


def tool_request(tool, arguments, special_tokens):
    return tool_message("tool", {"tool": tool, "args": arguments}, special_tokens)


# Function descriptors accepted by OpenAI-compatible chat/completions.
DEFINITIONS = [
    (
        "chat",
        "Append a message from the agent to the open window named chat; fails if it is not open.",
        {"content": {"type": "string"}},
    ),
    ("time_now", "Read the sample's synthetic clock.", {}),
    (
        "time_advance",
        "Advance synthetic time without real sleeping.",
        {"seconds": {"type": "number", "minimum": 0}},
    ),
    (
        "time_set",
        "Set synthetic local time. Null fields are deterministically randomized from "
        "the trajectory seed and call count; null years use 2000–2099. "
        "Timezone accepts an IANA name, UTC or ±HH:MM.",
        {
            **{
                name: {"type": ["integer", "null"], "minimum": low, "maximum": high}
                for name, low, high in (
                    ("year", 1, 9999),
                    ("month", 1, 12),
                    ("day", 1, 31),
                    ("hour", 0, 23),
                    ("minute", 0, 59),
                    ("second", 0, 59),
                )
            },
            "timezone": {"type": ["string", "null"], "maxLength": 128},
        },
    ),
    (
        "file_write",
        "Write a UTF-8 text file inside the virtual RAM filesystem.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
    ),
    ("file_read", "Read a virtual text file.", {"path": {"type": "string"}}),
    (
        "file_stat",
        "Get a file's immutable version reference without its content.",
        {"path": {"type": "string"}},
    ),
    (
        "file_patch",
        "Replace one unique text occurrence in a virtual file.",
        {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        },
    ),
    ("file_list", "List all virtual files.", {}),
    ("file_delete", "Delete a virtual file.", {"path": {"type": "string"}}),
    (
        "memory_save",
        "Save a searchable memory record.",
        {"key": {"type": "string"}, "text": {"type": "string"}},
    ),
    (
        "memory_search",
        "Retrieve memories ranked by lexical overlap (local RAG).",
        {"query": {"type": "string"}},
    ),
    (
        "note_save",
        "Create/update a task with optional owner, dependencies, blockers and acceptance criteria. Omitted optional fields are retained.",
        {
            "key": {"type": "string"},
            "text": {"type": "string"},
            "status": {"type": "string", "enum": ["todo", "doing", "blocked", "done"]},
        },
    ),
    ("note_list", "List tasks and notes.", {}),
    ("note_delete", "Delete a task/note.", {"key": {"type": "string"}}),
    (
        "skill_save",
        "Store a text skill in the sample sandbox.",
        {"name": {"type": "string"}, "content": {"type": "string"}},
    ),
    ("skill_load", "Load a sample sandbox skill.", {"name": {"type": "string"}}),
    ("skill_list", "List available sample skills.", {}),
    (
        "subagent",
        "Simulate delegation with an authored reply; returns conversationId. Supply resume to continue its retained conversation. Only explicitly referenced artifacts are shared.",
        {"task": {"type": "string"}},
    ),
    ("subagent_list", "List retained subagent identities and lifecycle states.", {}),
    (
        "subagent_get",
        "Inspect a retained subagent conversation.",
        {"conversationId": {"type": "string"}},
    ),
    (
        "subagent_close",
        "Close a conversation and retain its history; further resumes fail.",
        {"conversationId": {"type": "string"}},
    ),
    (
        "runtime_status",
        "Read synthetic time, completed call count, task counts and subagent lifecycle states.",
        {},
    ),
]
OPTIONAL = {
    "file_read": {"version": {"type": "string"}},
    "file_write": {"expected_version": {"type": "string"}},
    "file_patch": {"expected_version": {"type": "string"}},
    "note_save": {
        "owner": {"type": "string"},
        **{
            k: {"type": "array", "items": {"type": "string"}, "maxItems": 64}
            for k in ("depends_on", "blockers", "acceptance_criteria")
        },
    },
    "subagent": {
        "resume": {"type": "string"},
        "artifacts": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["path", "version"],
                "additionalProperties": False,
            },
        },
    },
}
STORES = ("files", "memory", "notes", "skills", "artifacts", "conversations")
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {**args, **OPTIONAL.get(name, {})},
                "required": list(args),
                "additionalProperties": False,
            },
        },
    }
    for name, desc, args in DEFINITIONS
]


class Sandbox:
    def __init__(
        self,
        seed="2030-01-01T09:00:00+00:00",
        seconds_per_call=1,
        max_calls=64,
        max_bytes=2_000_000,
        *,
        random_seed=None,
    ):
        self.clock = datetime.fromisoformat(seed)
        if self.clock.tzinfo is None:
            self.clock = self.clock.replace(tzinfo=UTC)
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

    def close(self):
        for name in STORES:
            getattr(self, name).clear()
        self.closed = True

    def snapshot(self):
        return deepcopy(
            {
                "clock": self.clock.isoformat(),
                "clock_timezone": getattr(self.clock.tzinfo, "key", None),
                "random_seed": self.random_seed,
                "calls": self.calls,
                **{name: getattr(self, name) for name in STORES},
            }
        )

    def restore(self, state):
        """Restore a compiler-produced snapshot when extending a trajectory."""
        self.clock = datetime.fromisoformat(state["clock"])
        if state.get("clock_timezone"):
            self.clock = self.clock.astimezone(ZoneInfo(state["clock_timezone"]))
        self.random_seed = state.get("random_seed", self.random_seed)
        self.calls = state["calls"]
        for name in STORES:
            setattr(self, name, deepcopy(state.get(name, {})))

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

    def check_args(self, name, args):
        schema = next((d[2] for d in DEFINITIONS if d[0] == name), None)
        if schema is None:
            raise ToolError(f"Unknown tool: {name}")
        allowed = {**schema, **OPTIONAL.get(name, {})}
        if (
            not isinstance(args, dict)
            or set(schema) - set(args)
            or set(args) - set(allowed)
        ):
            if name not in OPTIONAL:
                raise ToolError(f"{name} requires exactly: {', '.join(schema)}")
            raise ToolError(
                f"{name} requires: {', '.join(schema)}; optional: {', '.join(OPTIONAL[name])}"
            )
        for key, v in args.items():
            spec = allowed[key]
            types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
            if v is None and "null" in types:
                continue
            if "integer" in types and (
                type(v) is not int or not spec["minimum"] <= v <= spec["maximum"]
            ):
                raise ToolError(f"Invalid integer argument: {key}")
            if "string" in types and (
                not isinstance(v, str) or len(v) > spec.get("maxLength", 200_000)
            ):
                raise ToolError(f"Invalid string argument: {key}")
            if spec["type"] == "number" and (
                type(v) not in (int, float)
                or not math.isfinite(v)
                or v < 0
                or v > 31_536_000
            ):
                raise ToolError(f"Invalid seconds: {v}")
            if "enum" in spec and v not in spec["enum"]:
                raise ToolError(f"Invalid {key}")
            if spec["type"] == "array":
                if not isinstance(v, list) or len(v) > spec["maxItems"]:
                    raise ToolError(f"Invalid array argument: {key}")
                for entry in v:
                    if key == "artifacts":
                        if (
                            not isinstance(entry, dict)
                            or set(entry) != {"path", "version"}
                            or any(
                                not isinstance(s, str) or not s for s in entry.values()
                            )
                        ):
                            raise ToolError(
                                "Artifacts require path and version strings"
                            )
                    elif (
                        not isinstance(entry, str) or not entry or len(entry) > 200_000
                    ):
                        raise ToolError(f"Invalid string in {key}")
        if len(json.dumps(args).encode()) > self.max_bytes:
            raise ToolError("Argument quota exceeded")

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

    def start_call(self, name, args):
        if self.closed:
            raise ToolError("Sandbox has been destroyed")
        self.check_args(name, args)
        self.calls += 1
        if self.calls > self.max_calls:
            raise ToolError("Tool call budget exhausted")
        self.advance_clock(self.step)

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

        zones = components("timezone", (
            "UTC", "America/New_York", "America/Los_Angeles", "America/Sao_Paulo",
            "Europe/London", "Europe/Paris", "Africa/Johannesburg",
            "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
        ))
        years = range(2000, 2100) if args["year"] is None else [args["year"]]
        months = range(1, 13) if args["month"] is None else [args["month"]]
        dates = choices("date", (
            (year, month) for year in years for month in months
            if args["day"] is None or args["day"] <= monthrange(year, month)[1]
        ))
        hours = components("hour", range(24))
        minutes = components("minute", range(60))
        second = components("second", range(60))[0]
        for zone_name in zones:
            try:
                if re.fullmatch(r"(?:UTC)?[+-](?:[01]\d|2[0-3]):[0-5]\d", zone_name):
                    offset = zone_name.removeprefix("UTC")
                    seconds = (int(offset[1:3]) * 60 + int(offset[4:])) * 60
                    zone = timezone(timedelta(
                        seconds=seconds if offset[0] == "+" else -seconds
                    ))
                else:
                    zone = ZoneInfo(zone_name)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ToolError("Invalid timezone; use an IANA name, UTC or ±HH:MM") from error
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
                                valid = candidate.astimezone(UTC).astimezone(zone) == candidate
                            except (ValueError, OverflowError):
                                valid = False
                            if valid:
                                self.clock = candidate
                                return {"timestamp": self.clock.isoformat(), "synthetic": True}
        raise ToolError("Invalid date or nonexistent local time for the supplied fields")

    async def call(self, name, args, *, subagent_outcome=None, **context):
        if subagent_outcome is not None:
            if name != "subagent":
                raise ToolError("Only subagent accepts an authored outcome")
            validate_subagent_outcome(subagent_outcome)
        if name == "subagent":
            self.start_call(name, args)
            outcome = (
                deepcopy(subagent_outcome)
                if subagent_outcome is not None
                else {
                    "ok": False,
                    "error": "Synthetic subagent requires an authored outcome; supply it through MCP or a tool-call source event",
                }
            )
            result = outcome.get("result")
            recorded_id = (
                result.get("conversationId", outcome.get("conversationId"))
                if isinstance(result, dict)
                else outcome.get("conversationId")
            )
            conversation_id = self.begin_subagent(args, recorded_id)
            try:
                result = self.finish_subagent(conversation_id, outcome)
            except Exception as error:
                self.conversations[conversation_id]["status"] = "failed"
                raise ToolError(str(error), conversation_id) from error
            if not outcome["ok"]:
                raise ToolError(outcome["error"], conversation_id)
            return result
        return self.call_sync(name, args, **context)

    def call_sync(self, name, args, *, chat_open=False, agent_name="Kidra"):
        """Run deterministic capabilities without an event loop or network IO."""
        self.start_call(name, args)
        return self.execute_sync(name, args, chat_open=chat_open, agent_name=agent_name)

    def execute_sync(self, name, args, *, chat_open=False, agent_name="Kidra"):
        """Apply a validated deterministic operation; external events use no agent budget/time."""
        if name == "chat":
            if not chat_open:
                raise ToolError('No window named "chat" is open')
            if not isinstance(agent_name, str) or not agent_name.strip():
                raise ToolError("metadata.agent_name must be a nonempty string")
            return {
                "window": "chat",
                "content": f"<|msg|>{agent_name}: {args['content']}<|/msg|>\n",
            }
        if name == "time_now":
            return {"timestamp": self.clock.isoformat(), "synthetic": True}
        if name == "time_advance":
            self.advance_clock(args["seconds"])
            return {"timestamp": self.clock.isoformat(), "synthetic": True}
        if name == "time_set":
            return self.set_time(args)
        if name.startswith("file_"):
            if name == "file_list":
                return sorted(self.files)
            path = self.path(args["path"])
            if name == "file_write":
                return self.write_file(
                    path, args["content"], args.get("expected_version")
                )
            if name == "file_read" and "version" in args:
                key = self.artifact_key(path, args["version"])
                if key not in self.artifacts:
                    raise ToolError("Artifact version not found")
                return {
                    "path": path,
                    "version": args["version"],
                    "content": self.artifacts[key],
                }
            if path not in self.files:
                raise ToolError(f"Virtual file not found: {path}")
            if name == "file_read":
                return {
                    "path": path,
                    "version": self.file_version(path),
                    "content": self.files[path],
                }
            if name == "file_stat":
                return {
                    "path": path,
                    "version": self.file_version(path),
                    "bytes": len(self.files[path].encode()),
                }
            if name == "file_patch":
                if not args["old"] or self.files[path].count(args["old"]) != 1:
                    raise ToolError("Patch old text must match exactly once")
                return self.write_file(
                    path,
                    self.files[path].replace(args["old"], args["new"], 1),
                    args.get("expected_version"),
                )
            del self.files[path]
            return {"deleted": path}
        if name == "memory_save":
            return self.put(self.memory, args["key"], args["text"])
        if name == "memory_search":
            terms = set(re.findall(r"\w+", args["query"].lower()))
            ranked = []
            for key, text in self.memory.items():
                words = set(re.findall(r"\w+", text.lower()))
                score = len(terms & words) / max(1, len(terms | words))
                if score:
                    ranked.append({"key": key, "text": text, "score": score})
            return sorted(ranked, key=lambda r: (-r["score"], r["key"]))[:8]
        if name == "note_save":
            note = {
                **self.notes.get(args["key"], {}),
                **{k: deepcopy(v) for k, v in args.items() if k != "key"},
            }
            dependencies = note.get("depends_on", [])
            if args["key"] in dependencies:
                raise ToolError("A task cannot depend on itself")
            pending, visited = list(dependencies), set()
            while pending:
                key = pending.pop()
                if key == args["key"]:
                    raise ToolError("Task dependency cycle")
                if key not in visited:
                    visited.add(key)
                    pending.extend(self.notes.get(key, {}).get("depends_on", []))
            if note["status"] == "done" and (
                note.get("blockers")
                or any(
                    self.notes.get(dep, {}).get("status") != "done"
                    for dep in dependencies
                )
            ):
                raise ToolError("Task has blockers or unfinished dependencies")
            return self.put(
                self.notes,
                args["key"],
                note,
            )
        if name == "note_list":
            return deepcopy(self.notes)
        if name == "note_delete":
            if args["key"] not in self.notes:
                raise ToolError("Note not found")
            del self.notes[args["key"]]
            return {"deleted": args["key"]}
        if name == "skill_save":
            return self.put(self.skills, args["name"], args["content"])
        if name == "skill_list":
            return sorted(self.skills)
        if name == "skill_load":
            if args["name"] not in self.skills:
                raise ToolError("Skill not found")
            return {"name": args["name"], "content": self.skills[args["name"]]}
        if name == "subagent":
            raise ToolError("Subagent replay requires a recorded outcome")
        if name == "subagent_list":
            return [self.conversation_summary(k) for k in self.conversations]
        if name in ("subagent_get", "subagent_close"):
            cid = args["conversationId"]
            if cid not in self.conversations:
                raise ToolError("Unknown conversationId")
            if name == "subagent_close":
                if self.conversations[cid]["status"] == "running":
                    raise ToolError("Conversation is running")
                self.conversations[cid]["status"] = "closed"
            return {"conversationId": cid, **deepcopy(self.conversations[cid])}
        if name == "runtime_status":
            return {
                "clock": self.clock.isoformat(),
                "synthetic": True,
                "calls": self.calls,
                "tasks": {
                    status: sum(n["status"] == status for n in self.notes.values())
                    for status in ("todo", "doing", "blocked", "done")
                },
                "subagents": [self.conversation_summary(k) for k in self.conversations],
            }
        raise ToolError("Unsupported tool")

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
        self.check_args("subagent", args)
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
