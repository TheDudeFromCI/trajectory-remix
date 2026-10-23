"""JSON Patch diffs and bounded, sample-only session history."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime


def pointer_part(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def json_equal(before, after):
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(
            json_equal(before[k], after[k]) for k in before
        )
    if isinstance(before, list):
        return len(before) == len(after) and all(
            json_equal(a, b) for a, b in zip(before, after)
        )
    return before == after


def json_changes(before, after, path=""):
    """Return deterministic RFC 6902 add/remove/replace operations."""
    if json_equal(before, after):
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        operations = []
        for key in sorted(before.keys() - after.keys()):
            operations.append({"op": "remove", "path": path + "/" + pointer_part(key)})
        for key in sorted(after):
            at = path + "/" + pointer_part(key)
            if key not in before:
                operations.append(
                    {"op": "add", "path": at, "value": copy.deepcopy(after[key])}
                )
            else:
                operations.extend(json_changes(before[key], after[key], at))
        return operations
    if isinstance(before, list) and isinstance(after, list):
        # Trim equal ends so an insertion/deletion does not rewrite every sample.
        prefix = 0
        while prefix < min(len(before), len(after)) and json_equal(
            before[prefix], after[prefix]
        ):
            prefix += 1
        left, right = len(before), len(after)
        while (
            left > prefix
            and right > prefix
            and json_equal(before[left - 1], after[right - 1])
        ):
            left, right = left - 1, right - 1
        common = min(left, right)
        operations = []
        for index in range(prefix, common):
            operations.extend(
                json_changes(before[index], after[index], f"{path}/{index}")
            )
        for index in range(left - 1, common - 1, -1):
            operations.append({"op": "remove", "path": f"{path}/{index}"})
        for index in range(common, right):
            operations.append(
                {
                    "op": "add",
                    "path": f"{path}/{index}",
                    "value": copy.deepcopy(after[index]),
                }
            )
        return operations
    return [{"op": "replace", "path": path, "value": copy.deepcopy(after)}]


def apply_changes(document, operations):
    """Apply our generated patches to a copy; history values never become live references."""
    document = copy.deepcopy(document)
    for operation in operations:
        parts = [
            p.replace("~1", "/").replace("~0", "~")
            for p in operation["path"].split("/")[1:]
        ]
        if not parts:
            document = copy.deepcopy(operation["value"])
            continue
        parent = document
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        if operation["op"] == "remove":
            del parent[key]
        elif isinstance(parent, list) and operation["op"] == "add":
            parent.insert(key, copy.deepcopy(operation["value"]))
        else:
            parent[key] = copy.deepcopy(operation["value"])
    return document


def fingerprint(samples):
    data = json.dumps(
        samples, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode()
    return hashlib.sha256(data).hexdigest()


class HistoryError(ValueError):
    def __init__(self, message, code="conflict"):
        super().__init__(message)
        self.code = code


class SampleHistory:
    """Track source samples, excluding configuration, credentials and sandbox state."""

    def __init__(self, state, max_entries=50, max_bytes=32_000_000):
        self.state = state
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.undo_stack, self.redo_stack = [], []
        self.serial = 0
        self.tip = fingerprint(state["samples"]) if max_entries else None
        self.notice = None

    def commit(self, samples, label):
        if self.max_entries == 0:
            self.state["samples"] = samples
            self.state["revision"] += 1
            return self.state["revision"]
        before = self.state["samples"]
        digest = fingerprint(before)
        if digest != self.tip:
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.notice = "History reset after an untracked sample change"
        forward = json_changes(before, samples)
        if forward:
            inverse = json_changes(samples, before)
            self.redo_stack.clear()
            self.serial += 1
            entry = {
                "id": self.serial,
                "label": label,
                "time": datetime.now(UTC).isoformat(),
                "revision": self.state["revision"] + 1,
                "before": digest,
                "after": fingerprint(samples),
                "forward": forward,
                "inverse": inverse,
            }
            entry["bytes"] = len(json.dumps(entry, ensure_ascii=False).encode())
            self.undo_stack.append(entry)
            while (
                len(self.undo_stack) > self.max_entries
                or self.bytes_used() > self.max_bytes
            ):
                self.undo_stack.pop(0)
                self.notice = "Old edits were evicted to keep history within its limits"
        self.state["samples"] = samples
        self.state["revision"] += 1
        self.tip = fingerprint(samples)
        return self.state["revision"]

    def bytes_used(self):
        return sum(entry["bytes"] for entry in self.undo_stack + self.redo_stack)

    def view(self, include_changes=False):
        def summary(entry):
            result = {k: entry[k] for k in ("id", "label", "time", "revision")}
            result["operation_count"] = len(entry["forward"])
            if include_changes:
                result["changes"] = copy.deepcopy(entry["forward"])
            return result

        return {
            "revision": self.state["revision"],
            "undo": [summary(e) for e in reversed(self.undo_stack)],
            "redo": [summary(e) for e in reversed(self.redo_stack)],
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "bytes_used": self.bytes_used(),
            "max_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "notice": self.notice,
        }

    def restore(self, direction, steps):
        source, destination = (
            (self.undo_stack, self.redo_stack)
            if direction == "undo"
            else (self.redo_stack, self.undo_stack)
        )
        if steps > len(source):
            raise HistoryError(
                f"Only {len(source)} {direction} actions are available", "not_found"
            )
        current = self.state["samples"]
        restored = []
        for entry in reversed(source[-steps:]):
            expected = entry["after"] if direction == "undo" else entry["before"]
            if fingerprint(current) != expected:
                raise HistoryError(
                    "Samples changed outside edit history; undo/redo cannot overwrite them"
                )
            current = apply_changes(
                current, entry["inverse"] if direction == "undo" else entry["forward"]
            )
            restored.append(entry["id"])
        destination.extend(reversed(source[-steps:]))
        del source[-steps:]
        self.state["samples"] = current
        self.state["revision"] += 1
        self.tip = fingerprint(current)
        return {"revision": self.state["revision"], "actions": restored, "saved": False}


class SQLiteHistory(SampleHistory):
    """Bounded history of touched rows, with cross-process revision protection."""

    def __init__(self, state, max_entries=50, max_bytes=32_000_000):
        self.state = state
        self.store = state.store
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.undo_stack, self.redo_stack = [], []
        self.serial = 0
        self.tip = self.store.info()[1]
        self.notice = None

    def commit(self, samples, label, config=None):
        from .storage import SampleDraft

        if not isinstance(samples, SampleDraft) or samples.base is not self.store.samples:
            raise TypeError("SQLite history requires a sparse sample draft")
        before = self.store.info()[1]
        forward, inverse = samples.operations, samples.inverse
        revision = self.store.write(forward, samples.revision, config)
        if before != self.tip:
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.notice = "History reset after an external sample change"
        self.redo_stack.clear()
        if forward:
            self.serial += 1
            entry = {
                "id": self.serial, "label": label,
                "time": datetime.now(UTC).isoformat(), "revision": revision,
                "forward": copy.deepcopy(forward), "inverse": copy.deepcopy(inverse),
            }
            entry["bytes"] = len(json.dumps(entry, ensure_ascii=False).encode())
            self.undo_stack.append(entry)
            while len(self.undo_stack) > self.max_entries or self.bytes_used() > self.max_bytes:
                self.undo_stack.pop(0)
                self.notice = "Old edits were evicted to keep history within its limits"
        self.tip = revision
        return revision

    def view(self, include_changes=False):
        sample_revision = self.store.info()[1]
        if sample_revision != self.tip:
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.notice = "History reset after an external sample change"
            self.tip = sample_revision
        return super().view(include_changes)

    def restore(self, direction, steps):
        from .storage import StorageConflict

        source, destination = (self.undo_stack, self.redo_stack) if direction == "undo" else (self.redo_stack, self.undo_stack)
        revision, sample_revision, _, _ = self.store.info()
        if sample_revision != self.tip:
            raise HistoryError("Samples changed outside edit history; undo/redo cannot overwrite them")
        if steps > len(source):
            raise HistoryError(f"Only {len(source)} {direction} actions are available", "not_found")
        entries = list(reversed(source[-steps:]))
        operations = [op for entry in entries for op in entry["inverse" if direction == "undo" else "forward"]]
        try:
            revision = self.store.write(operations, revision)
        except StorageConflict as error:
            raise HistoryError(str(error)) from error
        destination.extend(entries)
        del source[-steps:]
        self.tip = revision
        return {"revision": revision, "actions": [e["id"] for e in entries], "saved": True}
