"""Bounded, reusable server-side previews. Expanded rows never cross the API."""

from collections import OrderedDict
import hashlib
import json
import time

from .compiler import FrameIndex, MAX_ROWS

MAX_PREVIEW_BYTES = 4_000_000
MAX_PREVIEW_FRAMES = 16


class PreviewLimit(ValueError):
    pass


class PreviewExpired(ValueError):
    pass


def bounded_json(value, message):
    """Serialize once and enforce an output budget, independently of compilation."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_PREVIEW_BYTES:
        raise PreviewLimit(message)
    return encoded


class Preview:
    def __init__(self, compiler, plan, plan_id):
        self.compiler, self.plan, self.plan_id = compiler, plan, plan_id
        self.index = FrameIndex(plan)
        self.last_used = time.monotonic()

    def frame(self, tick, windows=None):
        return self.compiler.frame(self.plan, tick, index=self.index, windows=windows)

    def metadata(self):
        # Timeline intervals retain exact ticks but omit token rows, event text,
        # tool arguments/results and historical sandbox snapshots.
        plan = self.plan
        return {
            **{key: plan[key] for key in (
                "sample_id", "trajectory_uuid", "max_window_slots", "slot_policy",
                "tokenizer_sha256", "special_tokens", "max_tick", "token_max_tick",
                "windows", "window_slots", "anchors", "diagnostics",
            )},
            "events": [{k: v for k, v in e.items() if k != "text"} for e in plan["events"]],
            "tool_calls": [{
                **{k: c[k] for k in (
                    "path", "tool", "window", "request_start", "start", "end",
                    "result_end", "result_anchor", "invisible", "cancelled",
                ) if k in c},
                "outcome": {"ok": c["outcome"]["ok"]},
            } for c in plan["tool_calls"]],
            "row_count": len(plan["rows"]),
            "plan_id": self.plan_id,
            "valid": True,
        }


class PreviewCache:
    """LRU shared by dashboard and MCP, bounded by entries, rows and idle time.

    At most one compiler-sized row budget is retained (rather than six maximal
    plans per interface). Indexes reference existing durable rows, not copies.
    """

    def __init__(self, compiler, *, max_rows=MAX_ROWS, max_entries=6, ttl=900):
        self.compiler = compiler
        self.max_rows, self.max_entries, self.ttl = max_rows, max_entries, ttl
        self.entries = OrderedDict()
        self.row_count = 0

    def _drop(self, key):
        self.row_count -= len(self.entries.pop(key).plan["rows"])

    def purge(self):
        now = time.monotonic()
        for key, preview in list(self.entries.items()):
            if now - preview.last_used >= self.ttl:
                self._drop(key)

    def get(self, plan_id):
        self.purge()
        preview = self.entries.get(plan_id)
        if preview is not None:
            preview.last_used = time.monotonic()
            self.entries.move_to_end(plan_id)
        return preview

    def compile(self, sample, *, revision=None):
        # Compiler configuration is fixed for this cache; tool definitions can
        # change and must participate in the key. Revision preserves tool-store
        # verification/pinning after edits (including saving a previously viewed
        # unsaved draft) and matches MCP's previous invalidation policy.
        key = json.dumps([revision, sample, self.compiler.tools.fingerprint], sort_keys=True)
        plan_id = hashlib.sha256(key.encode()).hexdigest()
        preview = self.get(plan_id)
        if preview is None:
            plan = self.compiler.compile(sample)
            count = len(plan["rows"])
            while self.entries and (
                len(self.entries) >= self.max_entries or self.row_count + count > self.max_rows
            ):
                self._drop(next(iter(self.entries)))
            preview = Preview(self.compiler, plan, plan_id)
            self.entries[plan_id] = preview
            self.row_count += count
        return preview
