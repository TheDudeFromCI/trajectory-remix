"""Capability sandbox: all tool state is JSON data in RAM, isolated per sample.

There is deliberately no Python execution, shell, OS file access, URL fetch, or
plugin import. The only outbound capability is an injected bounded LLM broker.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath


class ToolError(ValueError):
    pass


def tool_text(text, special_tokens, *, json_encoded=False):
    """Keep tool data from becoming model-owned control tokens."""
    escapes = {}
    for spelling in special_tokens:
        units = spelling.encode("utf-16-be")
        key = json.dumps(spelling, ensure_ascii=False)[1:-1] if json_encoded else spelling
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
        "file_write",
        "Write a UTF-8 text file inside the virtual RAM filesystem.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
    ),
    ("file_read", "Read a virtual text file.", {"path": {"type": "string"}}),
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
        "Create/update a task or note; status may be todo, doing or done.",
        {
            "key": {"type": "string"},
            "text": {"type": "string"},
            "status": {"type": "string", "enum": ["todo", "doing", "done"]},
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
        "Delegate a bounded task to the attached LLM; child has its own RAM sandbox.",
        {"task": {"type": "string"}},
    ),
]
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": args,
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
        broker=None,
        depth=0,
        max_calls=64,
        max_bytes=2_000_000,
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
        self.broker, self.depth = broker, depth
        self.max_calls, self.max_bytes, self.calls = max_calls, max_bytes, 0
        self.files, self.memory, self.notes, self.skills = {}, {}, {}, {}
        self.closed = False

    def close(self):
        for store in (self.files, self.memory, self.notes, self.skills):
            store.clear()
        self.closed = True

    def snapshot(self):
        return deepcopy(
            {
                "clock": self.clock.isoformat(),
                "calls": self.calls,
                "files": self.files,
                "memory": self.memory,
                "notes": self.notes,
                "skills": self.skills,
            }
        )

    def restore(self, state):
        """Restore a compiler-produced snapshot when extending a trajectory."""
        self.clock = datetime.fromisoformat(state["clock"])
        self.calls = state["calls"]
        for name in ("files", "memory", "notes", "skills"):
            setattr(self, name, deepcopy(state[name]))

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
        if not isinstance(args, dict) or set(args) != set(schema):
            raise ToolError(f"{name} requires exactly: {', '.join(schema)}")
        for key, spec in schema.items():
            v = args[key]
            if spec["type"] == "string" and (
                not isinstance(v, str) or len(v) > 200_000
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
        if len(json.dumps(args).encode()) > self.max_bytes:
            raise ToolError("Argument quota exceeded")

    def put(self, store, key, value):
        candidate = dict(store)
        candidate[key] = value
        total = sum(
            len(json.dumps(candidate if s is store else s).encode())
            for s in (self.files, self.memory, self.notes, self.skills)
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
        self.clock += timedelta(seconds=self.step)

    async def call(self, name, args, **context):
        if name == "subagent":
            self.start_call(name, args)
            if self.depth >= 2 or self.broker is None:
                raise ToolError("Subagent depth/capability limit")
            return {"response": await self.broker(args["task"], self.depth + 1)}
        return self.call_sync(name, args, **context)

    def call_sync(self, name, args, *, chat_open=False, agent_name="Kidra"):
        """Run deterministic capabilities without an event loop or network IO."""
        self.start_call(name, args)
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
            self.clock += timedelta(seconds=args["seconds"])
            return {"timestamp": self.clock.isoformat(), "synthetic": True}
        if name.startswith("file_"):
            if name == "file_list":
                return sorted(self.files)
            path = self.path(args["path"])
            if name == "file_write":
                return self.put(self.files, path, args["content"])
            if path not in self.files:
                raise ToolError(f"Virtual file not found: {path}")
            if name == "file_read":
                return {"path": path, "content": self.files[path]}
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
            return self.put(
                self.notes,
                args["key"],
                {"text": args["text"], "status": args["status"]},
            )
        if name == "note_list":
            return self.notes.copy()
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
        raise ToolError("Unsupported tool")
