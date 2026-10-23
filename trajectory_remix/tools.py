"""Capability sandbox: all tool state is JSON data in RAM, isolated per sample.

Host files, shell access and arbitrary network requests are never exposed.
Optional execution uses a separate resource-limited Docker capability.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath


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


SUBAGENT_INSTRUCTION = (
    "Complete the delegated task in this persistent conversation. "
    "Only explicitly referenced artifact data is available. "
    "No tools or other parent workspace context are available."
)


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
        "Delegate a task; returns conversationId. Supply resume to continue its retained conversation. Only explicitly referenced artifacts are shared.",
        {"task": {"type": "string"}},
    ),
    ("subagent_list", "List retained subagent identities and lifecycle states.", {}),
    (
        "subagent_wait",
        "Wait up to timeout_seconds for a background job; returns its current status and retained result.",
        {"jobId": {"type": "string"}},
    ),
    (
        "subagent_cancel",
        "Cancel an active background job; late results cannot modify the workspace.",
        {"jobId": {"type": "string"}},
    ),
    (
        "subagent_result",
        "Inspect a background job and retrieve its retained result.",
        {"jobId": {"type": "string"}},
    ),
    (
        "python_run",
        "Run Python against immutable virtual files in an opt-in Docker sandbox; no network, read-only inputs, bounded CPU/memory/time/output.",
        {"argv": {"type": "array", "items": {"type": "string"}, "maxItems": 32}},
    ),
    (
        "execution_get",
        "Retrieve a retained execution receipt, bounded output, exit status and exact input versions.",
        {"executionId": {"type": "string"}},
    ),
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
        "requires_evidence": {"type": "boolean"},
        **{
            k: {"type": "array", "items": {"type": "string"}, "maxItems": 64}
            for k in ("depends_on", "blockers", "acceptance_criteria", "evidence")
        },
    },
    "subagent": {
        "resume": {"type": "string"},
        "background": {"type": "boolean"},
        "role": {"type": "string"},
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
OPTIONAL["subagent_wait"] = {"timeout_seconds": {"type": "number", "maximum": 30}}
OPTIONAL["python_run"] = {
    "files": OPTIONAL["subagent"]["artifacts"],
    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 30},
}
INTERNAL = {
    "subagent_started": {"jobId": {"type": "string"}},
    "subagent_finished": {
        "jobId": {"type": "string"},
        "outcome": {"type": "object"},
        "elapsed_ms": {"type": "number"},
    },
    "execution_record": {"result": {"type": "object"}},
    "artifact_record": {
        "path": {"type": "string"},
        "version": {"type": "string"},
        "content": {"type": "string"},
    },
}
STORES = (
    "files",
    "memory",
    "notes",
    "skills",
    "artifacts",
    "conversations",
    "jobs",
    "executions",
)
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
        broker=None,
        depth=0,
        max_calls=64,
        max_bytes=2_000_000,
        executor=None,
        external_agents=False,
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
        self.executor, self.external_agents = executor, external_agents
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
                "calls": self.calls,
                **{name: getattr(self, name) for name in STORES},
            }
        )

    def restore(self, state):
        """Restore a compiler-produced snapshot when extending a trajectory."""
        self.clock = datetime.fromisoformat(state["clock"])
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
        schema = INTERNAL.get(
            name, next((d[2] for d in DEFINITIONS if d[0] == name), None)
        )
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
            if spec["type"] == "number" and (
                v < spec.get("minimum", 0) or v > spec.get("maximum", 31_536_000)
            ):
                raise ToolError(f"Argument outside bounds: {key}")
            if spec["type"] == "boolean" and type(v) is not bool:
                raise ToolError(f"Invalid boolean argument: {key}")
            if spec["type"] == "object" and not isinstance(v, dict):
                raise ToolError(f"Invalid object argument: {key}")
            if "enum" in spec and v not in spec["enum"]:
                raise ToolError(f"Invalid {key}")
            if spec["type"] == "array":
                if not isinstance(v, list) or len(v) > spec["maxItems"]:
                    raise ToolError(f"Invalid array argument: {key}")
                for entry in v:
                    if key in ("artifacts", "files"):
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
        self.clock += timedelta(seconds=self.step)

    async def call(self, name, args, **context):
        if name == "python_run":
            self.start_call(name, args)
            if self.executor is None:
                raise ToolError("Docker execution is not enabled for this session")
            files = self.execution_files(args.get("files"))
            result = await self.executor.run(
                files, args["argv"], args.get("timeout_seconds", 10)
            )
            self.record_execution(result)
            return result
        if name == "subagent":
            self.start_call(name, args)
            if self.depth >= 2 or (self.broker is None and not self.external_agents):
                raise ToolError("Subagent depth/capability limit")
            if args.get("background"):
                return self.start_job(args)
            if self.broker is None:
                raise ToolError("External workers require background=true")
            conversation_id = self.begin_subagent(
                args, None if "resume" in args else f"conversation-{uuid.uuid4().hex}"
            )
            conversation = self.conversations[conversation_id]
            try:
                response = await self.broker(
                    deepcopy(conversation["messages"]), self.depth + 1
                )
                return self.finish_subagent(
                    conversation_id, {"ok": True, "result": {"response": response}}
                )
            except Exception as error:
                conversation["status"] = "failed"
                raise ToolError(str(error), conversation_id) from error
            except BaseException:
                conversation["status"] = "failed"
                raise
        return self.call_sync(name, args, **context)

    def call_sync(self, name, args, *, chat_open=False, agent_name="Kidra"):
        """Run deterministic capabilities without an event loop or network IO."""
        self.start_call(name, args)
        if name in INTERNAL:
            raise ToolError(
                "Internal runtime events cannot be dispatched as model tools"
            )
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
            self.clock += timedelta(seconds=args["seconds"])
            return {"timestamp": self.clock.isoformat(), "synthetic": True}
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
            if note["status"] == "done" and note.get("requires_evidence"):
                evidence = note.get("evidence", [])
                if not evidence:
                    raise ToolError("Task requires execution evidence")
                for eid in evidence:
                    run = self.executions.get(eid)
                    if run is None or run["status"] != "passed":
                        raise ToolError(
                            "Task evidence must reference passing executions"
                        )
                    if any(
                        ref["path"] not in self.files
                        or self.file_version(ref["path"]) != ref["version"]
                        for ref in run["inputs"]
                    ):
                        raise ToolError("Task evidence refers to stale input files")
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
        if name in ("subagent_wait", "subagent_result", "subagent_cancel"):
            jid = args["jobId"]
            if jid not in self.jobs:
                raise ToolError("Unknown jobId")
            if name == "subagent_cancel" and self.jobs[jid]["status"] in (
                "queued",
                "running",
            ):
                self.jobs[jid]["status"] = "cancelled"
                self.conversations[self.jobs[jid]["conversationId"]][
                    "status"
                ] = "cancelled"
            return deepcopy(self.jobs[jid])
        if name == "subagent_started":
            job = self.jobs.get(args["jobId"])
            if job is None or job["status"] != "queued":
                raise ToolError("Job is not queued")
            job["status"] = "running"
            return {"jobId": args["jobId"], "status": "running"}
        if name == "subagent_finished":
            return self.finish_job(args["jobId"], args["outcome"], args["elapsed_ms"])
        if name == "execution_record":
            return self.record_execution(args["result"])
        if name == "execution_get":
            if args["executionId"] not in self.executions:
                raise ToolError("Unknown executionId")
            return deepcopy(self.executions[args["executionId"]])
        if name == "artifact_record":
            version = "sha256:" + hashlib.sha256(args["content"].encode()).hexdigest()
            if version != args["version"]:
                raise ToolError("Artifact content does not match its version")
            return self.put(
                self.artifacts,
                self.artifact_key(self.path(args["path"]), version),
                args["content"],
            )
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
                "jobs": [
                    {
                        k: deepcopy(v)
                        for k, v in job.items()
                        if k not in ("arguments", "outcome")
                    }
                    for job in self.jobs.values()
                ],
                "executions": {
                    key: value["status"] for key, value in self.executions.items()
                },
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
            **({"jobId": conversation["jobId"]} if "jobId" in conversation else {}),
        }

    def execution_files(self, references=None):
        refs = (
            references
            if references is not None
            else [
                {"path": path, "version": self.file_version(path)}
                for path in sorted(self.files)
            ]
        )
        files, seen = [], set()
        for ref in refs:
            path = self.path(ref["path"])
            key = self.artifact_key(path, ref["version"])
            if key not in self.artifacts or path in seen:
                raise ToolError("Execution requires unique, existing artifact versions")
            seen.add(path)
            files.append(
                {
                    "path": path,
                    "version": ref["version"],
                    "content": self.artifacts[key],
                }
            )
        if not files:
            raise ToolError("Execution requires at least one file")
        return files

    def record_execution(self, result):
        from .execution import validate_execution

        validate_execution(result, self)
        eid = result["executionId"]
        if eid in self.executions and self.executions[eid] != result:
            raise ToolError("Execution ID cannot be reused with different evidence")
        self.put(self.executions, eid, deepcopy(result))
        return deepcopy(result)

    def start_job(self, args, recorded=None):
        if len(self.jobs) >= 64:
            raise ToolError("Background job limit reached")
        recorded = recorded or {}
        jid = recorded.get("jobId", "job-" + uuid.uuid4().hex)
        if not isinstance(jid, str) or not jid or len(jid) > 128 or jid in self.jobs:
            raise ToolError("Invalid or duplicate jobId")
        previous = self.snapshot()
        try:
            cid = self.begin_subagent(
                args,
                recorded.get(
                    "conversationId",
                    None if "resume" in args else "conversation-" + uuid.uuid4().hex,
                ),
            )
            job = {
                "jobId": jid,
                "conversationId": cid,
                "status": "queued",
                "arguments": deepcopy(args),
            }
            self.put(self.jobs, jid, job)
            self.conversations[cid]["jobId"] = jid
        except ToolError:
            self.restore(previous)
            raise
        return {"jobId": jid, "conversationId": cid, "status": "queued"}

    def finish_job(self, jid, outcome, elapsed_ms):
        job = self.jobs.get(jid)
        if job is None or job["status"] not in ("queued", "running"):
            raise ToolError("Job is already terminal or unknown")
        if not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool:
            raise ToolError("Job completion requires a tool outcome")
        if type(elapsed_ms) is not int or elapsed_ms < 0:
            raise ToolError("Invalid job duration")
        if outcome["ok"]:
            result = outcome.get("result", {})
            if not isinstance(result, dict):
                raise ToolError("Invalid job result")
            if (
                not isinstance(result.get("response"), str)
                or not result["response"].strip()
                or len(result["response"]) > 200_000
            ):
                raise ToolError("Job result requires bounded nonempty response text")
            refs = result.get("artifacts", [])
            if (
                not isinstance(refs, list)
                or len(refs) > 128
                or any(
                    not isinstance(r, dict)
                    or set(r) != {"path", "version"}
                    or any(not isinstance(v, str) for v in r.values())
                    for r in refs
                )
            ):
                raise ToolError("Invalid job artifact references")
            for ref in refs:
                if (
                    self.artifact_key(self.path(ref["path"]), ref["version"])
                    not in self.artifacts
                ):
                    raise ToolError("Job result artifact not found")
            eids = result.get("executions", [])
            if not isinstance(eids, list) or any(
                not isinstance(eid, str) or eid not in self.executions for eid in eids
            ):
                raise ToolError("Job result execution not found")
            if "reviewed_artifacts" in result and result["reviewed_artifacts"] != job[
                "arguments"
            ].get("artifacts", []):
                raise ToolError("Review provenance differs from supplied context")
            worker = result.get("worker_id")
            conversation = self.conversations[job["conversationId"]]
            if worker is not None:
                if not isinstance(worker, str) or not worker or len(worker) > 128:
                    raise ToolError("Invalid worker identity")
                if conversation.get("worker_id", worker) != worker:
                    raise ToolError("Persistent conversation changed worker identity")
                conversation["worker_id"] = worker
        elif not isinstance(outcome.get("error"), str):
            raise ToolError("Failed job requires error text")
        self.finish_subagent(job["conversationId"], outcome)
        updated = {
            **job,
            "status": "completed" if outcome["ok"] else "failed",
            "elapsed_ms": elapsed_ms,
            "outcome": deepcopy(outcome),
        }
        self.put(self.jobs, jid, updated)
        return deepcopy(updated)

    def begin_subagent(self, args, conversation_id=None):
        """Dispatch a live or recorded turn using only context available now."""
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
                shared = {**ref, "path": path}
                if not args.get("background"):
                    shared["content"] = self.artifacts[key]
                artifacts.append(shared)
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
