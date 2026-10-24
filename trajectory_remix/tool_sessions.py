"""Ephemeral MCP workbenches for the same simulated tools used by generation."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from dataclasses import dataclass, field

from .tools import (
    Sandbox,
    ToolError,
    tool_failure,
    WindowProperties,
)

class ToolSessionError(ValueError):
    def __init__(self, message, code="invalid_arguments"):
        super().__init__(message)
        self.code = code


@dataclass
class ToolSession:
    sandbox: Sandbox
    options: dict
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    interactions: list = field(default_factory=list)


class ToolSessions:
    def __init__(self, max_sessions=8, max_log_bytes=4_000_000, registry=None):
        from .tools import default_registry
        self.registry = registry or default_registry()
        self.sessions = {}
        self.max_sessions, self.max_log_bytes = max_sessions, max_log_bytes

    def get(self, session_id):
        if session_id not in self.sessions:
            raise ToolSessionError(f"Tool session not found: {session_id}", "not_found")
        return self.sessions[session_id]

    def create(self, session_id, options):
        session_id = session_id if session_id is not None else str(uuid.uuid4())
        if not session_id or len(session_id) > 128:
            raise ToolSessionError("session_id must have 1..128 characters")
        if session_id in self.sessions:
            raise ToolSessionError("Tool session already exists", "conflict")
        if len(self.sessions) >= self.max_sessions:
            raise ToolSessionError(
                "Tool session limit reached; delete an unused session", "quota_exceeded"
            )
        sandbox = Sandbox(**options, registry=self.registry)
        self.sessions[session_id] = ToolSession(sandbox, options.copy())
        return self.inspect(session_id, include_state=False, include_log=False)

    def inspect(self, session_id, include_state=True, include_log=True):
        session = self.get(session_id)
        sandbox = session.sandbox
        result = {
            "session_id": session_id,
            "busy": session.lock.locked(),
            "options": session.options.copy(),
            "calls": sandbox.calls,
            "max_calls": sandbox.max_calls,
            "remaining_calls": max(0, sandbox.max_calls - sandbox.calls),
            "max_bytes": sandbox.max_bytes,
            "bytes_used": len(json.dumps(sandbox.state).encode()),
            "counts": {name: len(value) for name, value in sandbox.state.items() if isinstance(value, (dict, list))},
            "interaction_count": len(session.interactions),
            "max_log_bytes": self.max_log_bytes,
            "log_bytes": sum(
                len(json.dumps(e, ensure_ascii=False).encode())
                for e in session.interactions
            ),
        }
        if include_state:
            result["state"] = sandbox.snapshot()
        if include_log:
            result["interactions"] = copy.deepcopy(session.interactions)
        return result

    def definitions(self, session_id=None):
        if session_id is not None:
            self.get(session_id)
        definitions = self.registry.schemas(invisible=True)
        for definition in definitions:
            definition["available"] = True
        return {
            "tools": definitions,
            "requires_session": True,
            "note": "Calls run tick by tick against an opaque state dictionary. Invisible arguments require invisible-tool-calls.",
        }

    def reset(self, session_id):
        session = self.get(session_id)
        if session.lock.locked():
            raise ToolSessionError("Tool session has an in-flight call", "busy")
        sandbox = Sandbox(**session.options, registry=self.registry)
        session.sandbox.close()
        session.sandbox = sandbox
        session.interactions.clear()
        return self.inspect(session_id, include_state=False, include_log=False)

    def delete(self, session_id):
        session = self.get(session_id)
        if session.lock.locked():
            raise ToolSessionError("Tool session has an in-flight call", "busy")
        session.sandbox.close()
        del self.sessions[session_id]
        return {"deleted": session_id}

    def interaction(self, session_id, interaction_id):
        session = self.get(session_id)
        entry = next(
            (e for e in session.interactions if e["id"] == interaction_id), None
        )
        if entry is None:
            raise ToolSessionError(
                "Interaction not found or evicted from the bounded log", "not_found"
            )
        return copy.deepcopy(entry)

    async def call(
        self, session_id, name, arguments, recorder=None, context=None, outcome=None
    ):
        session = self.get(session_id)
        if session.lock.locked():
            raise ToolSessionError("Tool session has an in-flight call", "busy")
        try:
            session.sandbox.check_args(name, arguments)
        except ToolError as error:
            raise ToolSessionError(str(error)) from error
        if session.sandbox.calls >= session.sandbox.max_calls:
            raise ToolSessionError(
                "Tool call budget exhausted; reset the session", "quota_exceeded"
            )
        async with session.lock:
            candidate = copy.deepcopy(session.sandbox)
            updates = []
            call_context = dict(context or {})
            if "windows" in call_context:
                def window_write(name, before, after):
                    if before != after:
                        append = after.startswith(before)
                        updates.append({"window": name, "replace": not append,
                                        "content": after[len(before):] if append else after})
                call_context["windows"] = [WindowProperties(w.name, w.purpose, w.content, window_write)
                                           for w in call_context["windows"]]
            try:
                try:
                    result = await candidate.call(
                        name, arguments, recorded_outcome=outcome, **call_context
                    )
                    outcome = {"ok": True, "result": result}
                except (ToolError, ValueError, OverflowError) as error:
                    outcome = tool_failure(error)
                entry = {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "arguments": copy.deepcopy(arguments),
                    "outcome": copy.deepcopy(outcome),
                    "call_number": candidate.calls,
                    **({"window_updates": updates} if updates else {}),
                }
                size = len(json.dumps(entry, ensure_ascii=False).encode())
                if size > self.max_log_bytes:
                    raise ToolSessionError(
                        "Interaction exceeds the tool log limit", "quota_exceeded"
                    )
                if self.sessions.get(session_id) is not session:
                    raise ToolSessionError("Tool session was closed", "not_found")
                recording = recorder(entry) if recorder else None
                session.sandbox.close()
                session.sandbox = candidate
                session.interactions.append(entry)
                while (
                    sum(
                        len(json.dumps(e, ensure_ascii=False).encode())
                        for e in session.interactions
                    )
                    > self.max_log_bytes
                ):
                    session.interactions.pop(0)
                return {
                    "session_id": session_id,
                    "interaction": copy.deepcopy(entry),
                    "recording": recording,
                }
            except BaseException:
                candidate.close()
                raise

    def close(self):
        for session in self.sessions.values():
            session.sandbox.close()
        self.sessions.clear()
