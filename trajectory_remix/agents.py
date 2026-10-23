"""Background jobs with isolated, persistent child workspaces and external workers."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field

from .tools import Sandbox, ToolError, tool_failure, tool_message

ACTIVE = {"queued", "running"}
CHILD_TOOLS = {
    "file_write",
    "file_read",
    "file_list",
    "file_stat",
    "file_patch",
    "file_delete",
    "memory_save",
    "memory_search",
    "note_save",
    "note_list",
    "note_delete",
    "skill_save",
    "skill_load",
    "skill_list",
    "time_now",
    "python_run",
}


class VersionConflict(ToolError):
    pass


def completion_notices(events, jobs, special_tokens, window="tools"):
    """Worker files stay invisible; a bounded completion observation reaches Kidra."""
    notices = []
    for event in events:
        if event["tool"] != "subagent_finished":
            continue
        args = event["arguments"]
        outcome = args["outcome"]
        payload = {
            "jobId": args["jobId"],
            "conversationId": jobs[args["jobId"]]["conversationId"],
            "status": "completed" if outcome["ok"] else "failed",
            "elapsed_ms": args["elapsed_ms"],
        }
        if outcome["ok"]:
            payload.update(
                {
                    k: v
                    for k, v in outcome["result"].items()
                    if k in ("response", "artifacts", "executions")
                }
            )
        else:
            payload["error"] = outcome["error"]
        content = (
            tool_message("subagent_result", payload, special_tokens)
            .replace("{N_TICK", "\\u007bN_TICK")
            .replace("{WINDOW", "\\u007bWINDOW")
        )
        notices.append(
            {
                "op": "append",
                "window": window,
                "source": "tool",
                "content": content,
                "loss": {"mask": "exclude"},
            }
        )
    return notices


@dataclass
class Work:
    child: Sandbox
    original: dict
    base_versions: dict
    messages: list
    started: float = field(default_factory=time.monotonic)
    worker_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    trace: list = field(default_factory=list)
    writes: set = field(default_factory=set)
    broker: object = None


class AgentJobs:
    def __init__(self, sessions):
        self.sessions = sessions
        self.tasks, self.work, self.children, self.events = {}, {}, {}, {}
        self.active_tools = {}
        self.retired = set()
        self.brokers = {}
        self.semaphore = asyncio.Semaphore(8)

    def prepare(self, sid, jid):
        key = (sid, jid)
        if key in self.work:
            return self.work[key]
        session = self.sessions.get(sid)
        parent = session.sandbox
        job = parent.jobs.get(jid)
        if job is None:
            raise ToolError("Unknown jobId")
        if job["status"] not in ACTIVE:
            raise ToolError("Job is not active")
        cid = job["conversationId"]
        child_key = (sid, cid)
        self.brokers.setdefault(child_key, parent.broker)
        if child_key not in self.children:
            if sum(s == sid for s, _ in self.children) >= 16:
                raise ToolError("Subagent workspace limit reached (16)")
            self.children[child_key] = Sandbox(
                seed=parent.clock.isoformat(),
                seconds_per_call=0,
                depth=1,
                max_calls=128,
                max_bytes=min(512_000, parent.max_bytes),
                executor=parent.executor,
            )
        child = self.children[child_key]
        for ref in job["arguments"].get("artifacts", []):
            path = parent.path(ref["path"])
            child.write_file(
                path, parent.artifacts[parent.artifact_key(path, ref["version"])]
            )
        work = Work(
            child,
            copy.deepcopy(child.files),
            {p: parent.file_version(p) for p in parent.files},
            copy.deepcopy(parent.conversations[cid]["messages"]),
            broker=self.brokers[child_key],
        )
        self.work[key] = work
        self.events.setdefault(key, asyncio.Event())
        return work

    def adopt(self, sid):
        session = self.sessions.get(sid)
        for jid, job in session.sandbox.jobs.items():
            key = (sid, jid)
            if job["status"] not in ACTIVE:
                self.events.setdefault(key, asyncio.Event()).set()
                if key in self.tasks and not self.tasks[key].done():
                    # A completing task must not cancel itself.
                    if self.tasks[key] is not asyncio.current_task():
                        self.tasks[key].cancel()
                active = self.active_tools.get(key)
                if active is not None and active is not asyncio.current_task():
                    active.cancel()
            elif key not in self.work:
                try:
                    self.prepare(sid, jid)
                except ToolError as error:
                    self.tasks[key] = asyncio.create_task(self.fail(sid, jid, error))
                else:
                    if not session.sandbox.external_agents:
                        self.tasks[key] = asyncio.create_task(self.run(sid, jid))

    async def wait(self, sid, jid, timeout):
        session = self.sessions.get(sid)
        if jid not in session.sandbox.jobs:
            raise ToolError("Unknown jobId")
        if session.sandbox.jobs[jid]["status"] in ACTIVE:
            try:
                await asyncio.wait_for(
                    self.events.setdefault((sid, jid), asyncio.Event()).wait(), timeout
                )
            except asyncio.TimeoutError:
                pass

    async def change(self, sid, events, expected_versions=None):
        session = self.sessions.get(sid)
        async with session.lock:
            if expected_versions is not None:
                for path, expected in expected_versions.items():
                    current = (
                        session.sandbox.file_version(path)
                        if path in session.sandbox.files
                        else None
                    )
                    if current != expected:
                        raise VersionConflict(f"Shared file version conflict: {path}")
            candidate = copy.deepcopy(session.sandbox)
            try:
                for event in events:
                    candidate.check_args(event["tool"], event["arguments"])
                    candidate.execute_sync(event["tool"], event["arguments"])
                if session.on_events:
                    state = session.on_events(events)
                    if state is not None:
                        candidate.restore(state)
                session.sandbox.close()
                session.sandbox = candidate
            except BaseException:
                candidate.close()
                raise

    async def claim(self, sid, jid, worker_id):
        session = self.sessions.get(sid)
        if not session.sandbox.external_agents:
            raise ToolError("This session uses its configured LLM worker")
        work = self.prepare(sid, jid)
        async with work.lock:
            job = self.sessions.get(sid).sandbox.jobs.get(jid)
            if not job or job["status"] not in ACTIVE:
                raise ToolError("Job is not active")
            if work.worker_id not in (None, worker_id):
                raise ToolError("Job is claimed by another worker")
            cid = job["conversationId"]
            prior = next(
                (
                    w.worker_id
                    for (s, j), w in self.work.items()
                    if s == sid
                    and j != jid
                    and self.sessions.get(s).sandbox.jobs[j]["conversationId"] == cid
                    and w.worker_id
                ),
                None,
            )
            if prior is not None and prior != worker_id:
                raise ToolError("Resume requires the same persistent external worker")
            if job["status"] == "queued":
                await self.change(
                    sid, [self.event("subagent_started", {"jobId": jid}, worker_id)]
                )
            work.worker_id = worker_id
            return {
                "jobId": jid,
                "conversationId": cid,
                "worker_id": worker_id,
                "messages": copy.deepcopy(work.messages),
                "files": [
                    {"path": p, "version": work.child.file_version(p)}
                    for p in sorted(work.child.files)
                ],
                "tools": sorted(CHILD_TOOLS),
            }

    @staticmethod
    def event(tool, args, source):
        return {
            "op": "invisible-tool-call",
            "tool": tool,
            "arguments": args,
            "source": source,
        }

    async def tool(self, sid, jid, worker_id, name, args):
        work = self.work.get((sid, jid))
        if work is None or work.worker_id != worker_id:
            raise ToolError("Worker must claim this job first")
        if name not in CHILD_TOOLS:
            raise ToolError("Tool is not available to child workers")
        async with work.lock:
            if self.sessions.get(sid).sandbox.jobs[jid]["status"] not in ACTIVE:
                raise ToolError("Job is not active")
            candidate = copy.deepcopy(work.child)
            self.active_tools[(sid, jid)] = asyncio.current_task()
            try:
                try:
                    result = await candidate.call(name, args)
                    outcome = {"ok": True, "result": result}
                except (ValueError, OverflowError) as error:
                    outcome = tool_failure(error)
                if self.sessions.get(sid).sandbox.jobs[jid]["status"] not in ACTIVE:
                    raise ToolError(
                        "Job ended while its tool was running; result discarded"
                    )
                summary = {"tool": name, "ok": outcome["ok"]}
                if name.startswith("file_") and "path" in args:
                    summary["path"] = candidate.path(args["path"])[:256]
                    if (
                        name in ("file_write", "file_patch", "file_delete")
                        and outcome["ok"]
                    ):
                        work.writes.add(candidate.path(args["path"]))
                if name == "python_run" and outcome["ok"]:
                    summary.update(
                        executionId=result["executionId"], status=result["status"]
                    )
                work.trace.append(summary)
                work.child.close()
                work.child = candidate
                cid = self.sessions.get(sid).sandbox.jobs[jid]["conversationId"]
                self.children[(sid, cid)] = candidate
                return {
                    "outcome": outcome,
                    "remaining_calls": max(0, candidate.max_calls - candidate.calls),
                }
            except asyncio.CancelledError:
                candidate.close()
                if (
                    sid in self.sessions.sessions
                    and self.sessions.get(sid).sandbox.jobs[jid]["status"]
                    == "cancelled"
                ):
                    raise ToolError(
                        "Job was cancelled; worker tool execution stopped"
                    ) from None
                raise
            except BaseException:
                candidate.close()
                raise
            finally:
                self.active_tools.pop((sid, jid), None)

    async def complete(self, sid, jid, worker_id, response, error=None):
        work = self.work.get((sid, jid))
        if work is None or work.worker_id != worker_id:
            raise ToolError("Worker must claim this job first")
        async with work.lock:
            parent = self.sessions.get(sid).sandbox
            job = parent.jobs.get(jid)
            if not job or job["status"] not in ACTIVE:
                raise ToolError("Job is already terminal; late completion rejected")
            source = worker_id
            events = []
            expected_versions = {}
            outcome = (
                {"ok": False, "error": error}
                if error
                else {"ok": True, "result": {"response": response}}
            )
            if not error:
                changed = {
                    p: text
                    for p, text in work.child.files.items()
                    if work.original.get(p) != text or p in work.writes
                }
                deleted = [p for p in work.original if p not in work.child.files]
                for path in (*changed, *deleted):
                    expected_versions[path] = work.base_versions.get(path)
                    current = (
                        parent.file_version(path) if path in parent.files else None
                    )
                    if current != work.base_versions.get(path):
                        outcome = {
                            "ok": False,
                            "error": f"Shared file version conflict: {path}",
                        }
                        break
                else:
                    # Historical inputs must remain available for evidence validation.
                    for run in work.child.executions.values():
                        for ref in run["inputs"]:
                            key = parent.artifact_key(ref["path"], ref["version"])
                            if (
                                key not in parent.artifacts
                                and changed.get(ref["path"])
                                != work.child.artifacts[key]
                            ):
                                events.append(
                                    self.event(
                                        "artifact_record",
                                        {**ref, "content": work.child.artifacts[key]},
                                        source,
                                    )
                                )
                    for path, content in changed.items():
                        events.append(
                            self.event(
                                "file_write", {"path": path, "content": content}, source
                            )
                        )
                    for path in deleted:
                        events.append(self.event("file_delete", {"path": path}, source))
                    for eid, run in work.child.executions.items():
                        if eid not in parent.executions:
                            events.append(
                                self.event("execution_record", {"result": run}, source)
                            )
                    outcome["result"].update(
                        artifacts=[
                            {"path": p, "version": work.child.file_version(p)}
                            for p in sorted(changed)
                        ],
                        executions=list(work.child.executions),
                        worker_id=worker_id,
                        tool_calls=copy.deepcopy(work.trace),
                        reviewed_artifacts=copy.deepcopy(
                            job["arguments"].get("artifacts", [])
                        ),
                    )
            elapsed_ms = int((time.monotonic() - work.started) * 1000)
            events.append(
                self.event(
                    "subagent_finished",
                    {"jobId": jid, "outcome": outcome, "elapsed_ms": elapsed_ms},
                    source,
                )
            )
            try:
                await self.change(
                    sid, events, expected_versions if outcome["ok"] else None
                )
            except VersionConflict as conflict:
                # Another publication may win while this completion waits for the
                # parent lock. End the losing job without publishing any files.
                await self.change(
                    sid,
                    [
                        self.event(
                            "subagent_finished",
                            {
                                "jobId": jid,
                                "outcome": tool_failure(conflict),
                                "elapsed_ms": elapsed_ms,
                            },
                            source,
                        )
                    ],
                )
            self.events[(sid, jid)].set()
            return copy.deepcopy(self.sessions.get(sid).sandbox.jobs[jid])

    async def fail(self, sid, jid, error):
        try:
            await self.change(
                sid,
                [
                    self.event(
                        "subagent_finished",
                        {
                            "jobId": jid,
                            "outcome": tool_failure(error),
                            "elapsed_ms": 0,
                        },
                        "runtime",
                    )
                ],
            )
        finally:
            self.events.setdefault((sid, jid), asyncio.Event()).set()

    async def run(self, sid, jid):
        try:
            async with self.semaphore:
                session = self.sessions.get(sid)
                if session.sandbox.jobs[jid]["status"] not in ACTIVE:
                    return
                worker_id = (
                    "configured-llm:" + session.sandbox.jobs[jid]["conversationId"]
                )
                await self.change(
                    sid, [self.event("subagent_started", {"jobId": jid}, worker_id)]
                )
                work = self.work[(sid, jid)]
                work.worker_id = worker_id
                response = await work.broker(
                    copy.deepcopy(work.messages),
                    1,
                    lambda name, args: self.tool(sid, jid, worker_id, name, args),
                )
                await self.complete(sid, jid, worker_id, response)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if (
                sid not in self.sessions.sessions
                or self.sessions.get(sid).sandbox.jobs[jid]["status"] not in ACTIVE
            ):
                return
            try:
                await self.fail(
                    sid, jid, ToolError("Background worker failed: " + str(error))
                )
            except Exception:
                # Never leak an unhandled task exception; preserve a visible recording error.
                if sid in self.sessions.sessions:
                    self.sessions.get(sid).recording_error = (
                        "Background result could not be recorded; inspect source changes before continuing"
                    )

    def discard(self, sid=None):
        for (s, _), task in list(self.active_tools.items()):
            if sid is None or sid == s:
                task.cancel()
        for (s, _), task in list(self.tasks.items()):
            if sid is None or sid == s:
                task.cancel()
                self.retired.add(task)
                task.add_done_callback(self.retired.discard)
        for key, child in list(self.children.items()):
            if sid is None or key[0] == sid:
                child.close()
                del self.children[key]
        for mapping in (self.work, self.events, self.tasks, self.brokers):
            for key in list(mapping):
                if sid is None or key[0] == sid:
                    if mapping is self.events:
                        mapping[key].set()
                    del mapping[key]
