"""Typed trajectory commands shared by the local API and the MCP bridge.

Edits operate on copies and commit once. Invalid drafts can be repaired across
several commands; validate=True makes a single edit conditional on compilation.
"""

from __future__ import annotations

import copy
import difflib
import inspect
import json
import re
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from typing import Annotated, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .changes import HistoryError, SampleHistory, json_changes, json_equal
from .compiler import KEY, CompileError
from .dataset import new_sample, prepare_sample
from .llm import LLMClient, SampleBuilder
from .metadata import MetadataFilter, query as query_metadata, counts as metadata_counts
from .storage import SampleDraft, SQLiteSamples, StorageConflict
from .tool_sessions import ToolSessionError, ToolSessions
from .tools import ToolError, chat_context, tool_message, tool_request, tool_text

Nonnegative = Annotated[int, Field(ge=0)]
PageSize = Annotated[int, Field(ge=1, le=5000)]
Revision = Nonnegative | None
EditName = Literal[
    "create_trajectory",
    "duplicate_trajectory",
    "replace_trajectory",
    "delete_trajectory",
    "update_trajectory_metadata",
    "create_event",
    "create_tool_call",
    "update_event",
    "move_event",
    "duplicate_event",
    "patch_event_content",
    "delete_event",
    "rename_window",
]


class BatchEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: EditName
    arguments: dict


class CommandError(ValueError):
    def __init__(self, message, code="invalid_arguments"):
        super().__init__(message)
        self.code = code


class TextPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: Nonnegative
    end: Nonnegative
    text: str


def command(*, write=False, destructive=False, open_world=False):
    def decorate(method):
        hints = get_type_hints(method, include_extras=True)
        fields = {
            ("validate_" if name == "validate" else name): (
                hints[name],
                Field(default=... if p.default is p.empty else p.default, alias=name),
            )
            for name, p in inspect.signature(method).parameters.items()
            if name != "self"
        }
        method.command_model = create_model(
            method.__name__ + "Arguments",
            __config__=ConfigDict(extra="forbid", strict=True),
            **fields,
        )
        method.command_annotations = {
            "readOnlyHint": not write,
            "destructiveHint": destructive,
            "openWorldHint": open_world or method.__name__ == "validate_trajectory",
        }
        return method

    return decorate


def merge_patch(target, patch):
    """RFC 7396: objects merge, null deletes, arrays/scalars replace."""
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = merge_patch(result.get(key), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class TrajectoryCommands:
    def __init__(self, state, compiler, runner, client=None, history=None):
        self.state, self.compiler, self.runner = state, compiler, runner
        self.client = client
        self.history = history if history is not None else runner.history
        self.action = ContextVar("trajectory_command", default="Edit trajectory")
        self.edit_revision = ContextVar("trajectory_revision", default=None)
        self.tool_sessions = ToolSessions()
        self.cache = OrderedDict()
        self.commands = {
            name: getattr(self, name)
            for name in dir(type(self))
            if hasattr(getattr(type(self), name), "command_model")
        }

    def tools(self):
        return [
            {
                "name": name,
                "description": inspect.getdoc(fn),
                "inputSchema": fn.command_model.model_json_schema(),
                "annotations": fn.command_annotations,
            }
            for name, fn in self.commands.items()
        ]

    async def call(self, name, arguments):
        if name not in self.commands:
            raise CommandError(f"Unknown tool: {name}", "unknown_tool")
        fn = self.commands[name]
        try:
            model = fn.command_model.model_validate(arguments)
        except ValidationError as error:
            raise CommandError(str(error)) from error
        # Preserve nested TextPatch objects rather than dumping them to dicts.
        token = self.action.set(name)
        revision_token = self.edit_revision.set(self.state["revision"])
        try:
            result = fn(
                **{
                    field.alias: getattr(model, name)
                    for name, field in type(model).model_fields.items()
                }
            )
            if inspect.isawaitable(result):
                result = await result
            return copy.deepcopy(result)
        except (HistoryError, ToolSessionError, StorageConflict) as error:
            raise CommandError(str(error), error.code) from error
        finally:
            self.action.reset(token)
            self.edit_revision.reset(revision_token)

    def _find(self, sample_id):
        samples = self.state["samples"]
        if isinstance(samples, (SQLiteSamples, SampleDraft)):
            found = samples.find(sample_id)
            if found:
                return found
            raise CommandError(f"Trajectory not found: {sample_id}", "not_found")
        for index, sample in enumerate(self.state["samples"]):
            if sample.get("id") == sample_id:
                return index, sample
        raise CommandError(f"Trajectory not found: {sample_id}", "not_found")

    def _editable(self, expected_revision):
        if self.runner.status["running"]:
            raise CommandError("Stop generation before editing the dataset", "busy")
        if (
            expected_revision is not None
            and expected_revision != self.state["revision"]
        ):
            raise CommandError(
                "Dataset changed; read it again before editing", "conflict"
            )

    def _commit(self, sample, index=None, validate=False):
        sample = prepare_sample(sample, self.state["samples"][index] if index is not None else None)
        self.compiler.slots_for(sample)
        ident = sample.get("id")
        if not isinstance(ident, str) or not ident:
            raise CommandError("A nonempty sample id is required")
        samples = self.state["samples"]
        if isinstance(samples, (SQLiteSamples, SampleDraft)):
            found = samples.find(ident)
            duplicate = found is not None and found[0] != index
        else:
            duplicate = any(i != index and other.get("id") == ident for i, other in enumerate(samples))
        if duplicate:
            raise CommandError(f"Duplicate sample ID: {ident}", "conflict")
        # Reject non-JSON values before changing the live session.
        json.dumps(sample, allow_nan=False)
        if validate:
            self.compiler.compile(sample)
        updated = self.state["samples"].copy()
        if hasattr(self.state, "store") and self.edit_revision.get() is not None:
            updated.revision = self.edit_revision.get()
        if index is None:
            updated.append(sample)
        else:
            updated[index] = sample
        revision = self.history.commit(updated, f"{self.action.get()}: {ident}")
        return {"sample_id": ident, "revision": revision, "saved": hasattr(self.state, "store")}

    def _draft(self, sample_id, expected_revision):
        self._editable(expected_revision)
        index, sample = self._find(sample_id)
        return index, copy.deepcopy(sample)

    def _replay(self, sample):
        replay = sample.get("replay")
        if not isinstance(replay, dict):
            raise CommandError(
                "replay must be an object; repair it with replace_trajectory"
            )
        return replay

    def _event(self, sample, tick, event_index):
        group = self._replay(sample).get(tick)
        if not isinstance(group, list) or not 0 <= event_index < len(group):
            raise CommandError(
                "Event not found; use list_events for current addresses", "not_found"
            )
        return group[event_index]

    def _insert(self, sample, tick, event, position):
        if not KEY.fullmatch(tick):
            raise CommandError("tick must be an integer string or {N_TICK:n}+offset")
        replay = self._replay(sample)
        group = replay.setdefault(tick, [])
        if not isinstance(group, list):
            raise CommandError("Target tick must contain an event array")
        if position is None:
            position = len(group)
        if not 0 <= position <= len(group):
            raise CommandError("position is outside the target event array")
        group.insert(position, copy.deepcopy(event))
        return {"tick": tick, "event_index": position}

    def _remove(self, sample, tick, event_index):
        self._event(sample, tick, event_index)
        replay = self._replay(sample)
        event = replay[tick].pop(event_index)
        if not replay[tick]:
            del replay[tick]
        return event

    def _plan(self, sample_id):
        _, sample = self._find(sample_id)
        key = (self.state["revision"], sample_id)
        if key not in self.cache:
            self.cache[key] = self.compiler.compile(sample)
            while len(self.cache) > 6:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def _range(self, plan, start_tick, end_tick):
        end_tick = plan["max_tick"] if end_tick is None else end_tick
        if not 0 <= start_tick <= end_tick <= plan["max_tick"]:
            raise CommandError(f"Range must be within 0..{plan['max_tick']}, inclusive")
        return end_tick

    @command()
    def get_session(self):
        """View dataset revision, generation status, tokenizer and special token spellings."""
        return {
            "revision": self.state["revision"],
            "sample_count": len(self.state["samples"]),
            "max_window_slots": self.compiler.max_window_slots,
            "slot_policy": self.compiler.slot_policy,
            "status": self.runner.status,
            "tokenizer_sha256": self.compiler.fingerprint,
            "special_tokens": self.compiler.special,
        }

    @command()
    def list_trajectories(
        self, query: str = "", offset: Nonnegative = 0, limit: PageSize = 50
    ):
        """List trajectories with metadata; filter by ID or metadata text and paginate."""
        if isinstance(self.state["samples"], SQLiteSamples):
            revision = self.state["revision"]
            total, trajectories = self.state["samples"].store.listing(query, offset, limit)
            return {"revision": revision, "total": total, "trajectories": trajectories}
        samples = [
            s
            for s in self.state["samples"]
            if query.casefold()
            in (
                str(s.get("id", ""))
                + json.dumps(s.get("metadata", {}), ensure_ascii=False)
            ).casefold()
        ]
        return {
            "revision": self.state["revision"],
            "total": len(samples),
            "trajectories": [
                {k: v for k, v in s.items() if k != "replay"}
                for s in samples[offset : offset + limit]
            ],
        }

    @command()
    def query_trajectories(
        self, filters: list[MetadataFilter] | None = None, tags: list[str] | None = None,
        tag_mode: Literal["all", "any"] = "all", offset: Nonnegative = 0, limit: PageSize = 100,
    ):
        """Query scalar metadata keys, tag membership and cached counts without reading replay.

        Filters use {key, op, value}; op is eq/ne/lt/lte/gt/gte/exists. Reserved
        counts.events, counts.windows and counts.tags address cached integer columns.
        Keys otherwise address literal top-level metadata properties, including punctuation.
        All filters combine with AND. Tags support all/any. Results include UUID and content hash.
        """
        return query_metadata(self.state.store, filters, tags, tag_mode, offset, limit)

    @command()
    def get_metadata_counts(
        self, key: str = "tags", filters: list[MetadataFilter] | None = None,
        tags: list[str] | None = None, tag_mode: Literal["all", "any"] = "all", limit: PageSize = 100,
    ):
        """Group matching trajectories by a scalar metadata key, tags, or counts.* field.

        Returns bounded value/count groups and total matching trajectories. Tag groups
        count each trajectory once per distinct tag. Nested objects/arrays are omitted.
        """
        return metadata_counts(self.state.store, key, filters, tags, tag_mode, limit)

    @command()
    def get_trajectory(self, sample_id: str):
        """Return the complete, untruncated source sample JSON, including invalid drafts."""
        revision = self.state["revision"]
        return {"sample": self._find(sample_id)[1], "revision": revision}

    @command(write=True)
    def create_trajectory(
        self,
        sample_id: str | None = None,
        sample: dict | None = None,
        template: Literal["empty", "demo"] = "empty",
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Create a trajectory from full JSON, an empty draft, or the dashboard demo template.

        Empty drafts need events before they compile. IDs default to UUIDs.
        """
        self._editable(expected_revision)
        if sample is None:
            sample = new_sample()
            if template == "empty":
                sample["replay"] = {"0": []}
            else:
                for group in sample["replay"].values():
                    for event in group:
                        if "content" in event:
                            event["content"] = event["content"].replace(
                                "<|eos|>", self.compiler.special["eos"]
                            )
        else:
            sample = copy.deepcopy(sample)
        sample["id"] = (
            sample_id if sample_id is not None else sample.get("id", str(uuid.uuid4()))
        )
        return self._commit(sample, validate=validate)

    @command(write=True)
    def duplicate_trajectory(
        self,
        sample_id: str,
        new_sample_id: str | None = None,
        expected_revision: Revision = None,
    ):
        """Deep-copy a trajectory and all metadata under a fresh unique ID."""
        _, sample = self._draft(sample_id, expected_revision)
        sample["id"] = new_sample_id if new_sample_id is not None else str(uuid.uuid4())
        sample.pop("uuid", None)
        return self._commit(sample)

    @command(write=True, destructive=True)
    def replace_trajectory(
        self,
        sample_id: str,
        sample: dict,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Replace the complete source JSON. validate=True rejects invalid changes atomically."""
        index, _ = self._draft(sample_id, expected_revision)
        return self._commit(copy.deepcopy(sample), index, validate)

    @command(write=True, destructive=True)
    def delete_trajectory(self, sample_id: str, expected_revision: Revision = None):
        """Remove a trajectory from the SQLite dataset immediately."""
        index, _ = self._draft(sample_id, expected_revision)
        updated = self.state["samples"].copy()
        if hasattr(self.state, "store") and self.edit_revision.get() is not None:
            updated.revision = self.edit_revision.get()
        updated.pop(index)
        revision = self.history.commit(updated, f"delete_trajectory: {sample_id}")
        return {
            "sample_id": sample_id,
            "revision": revision,
            "saved": hasattr(self.state, "store"),
        }

    @command()
    def get_trajectory_metadata(self, sample_id: str):
        """View every top-level field except replay: ID, defaults, schema, policy and metadata."""
        sample = self._find(sample_id)[1]
        return {
            "attributes": {k: v for k, v in sample.items() if k != "replay"},
            "revision": self.state["revision"],
        }

    @command(write=True, destructive=True)
    def update_trajectory_metadata(
        self,
        sample_id: str,
        patch: dict,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Merge-patch top-level attributes (RFC 7396); null deletes and arrays replace.

        Example: {"metadata":{"tags":["reviewed"]},"defaults":{"implicit-noop-loss-weight":0.2}}.
        replay cannot be changed here. id is a unique editable label; uuid is immutable.
        """
        index, sample = self._draft(sample_id, expected_revision)
        if "replay" in patch:
            raise CommandError(
                "Use event commands or replace_trajectory to edit replay"
            )
        return self._commit(merge_patch(sample, patch), index, validate)

    @command()
    def list_events(
        self,
        sample_id: str,
        window: str | None = None,
        offset: Nonnegative = 0,
        limit: PageSize = 100,
    ):
        """List raw events and their tick/event_index addresses, even in invalid drafts.

        Addresses use current zero-based array indexes and change after insertion/deletion.
        Numeric tick keys sort first, then symbolic keys, matching compiler order.
        """
        replay = self._replay(self._find(sample_id)[1])
        events = []
        for tick in sorted(
            replay, key=lambda k: (0, int(k)) if k.isdigit() else (1, k)
        ):
            if not isinstance(replay[tick], list):
                raise CommandError(
                    "Tick events must be arrays; repair with replace_trajectory"
                )
            for i, event in enumerate(replay[tick]):
                if (
                    window is None
                    or isinstance(event, dict)
                    and event.get("window") == window
                ):
                    events.append({"tick": tick, "event_index": i, "event": event})
        return {
            "events": events[offset : offset + limit],
            "total": len(events),
            "revision": self.state["revision"],
        }

    @command()
    def get_event(self, sample_id: str, tick: str, event_index: Nonnegative):
        """Read one complete raw event by its tick key and zero-based array index."""
        return {
            "event": self._event(self._find(sample_id)[1], tick, event_index),
            "revision": self.state["revision"],
        }

    @command(write=True)
    def create_event(
        self,
        sample_id: str,
        tick: str,
        event: dict,
        position: Nonnegative | None = None,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Insert an open, append, generate or tool-call event at a numeric or anchor tick key.

        position defaults to the end of the tick array. Close via generated EOS.
        """
        index, sample = self._draft(sample_id, expected_revision)
        address = self._insert(sample, tick, event, position)
        return {**self._commit(sample, index, validate), **address}

    @command(write=True, destructive=True)
    def update_event(
        self,
        sample_id: str,
        tick: str,
        event_index: Nonnegative,
        settings: dict,
        remove_fields: list[str] | None = None,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Set event fields such as window, op, source, delivery, loss or tokens-per-tick.

        Settings replace individual fields; remove_fields clears optional fields.
        Use move_event for tick/offset changes and patch_event_content for text/segments.
        """
        index, sample = self._draft(sample_id, expected_revision)
        event = self._event(sample, tick, event_index)
        if not isinstance(event, dict):
            raise CommandError(
                "Event must be an object; delete it and create a replacement"
            )
        if {"content", "segments"}.intersection(settings) or {
            "content",
            "segments",
        }.intersection(remove_fields or []):
            raise CommandError("Use patch_event_content for content or segments")
        event.update(copy.deepcopy(settings))
        for field in remove_fields or []:
            event.pop(field, None)
        return self._commit(sample, index, validate)

    @command(write=True, destructive=True)
    def move_event(
        self,
        sample_id: str,
        tick: str,
        event_index: Nonnegative,
        to_tick: str | None = None,
        tick_offset: int | None = None,
        position: Nonnegative | None = None,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Move/reorder an event. Choose to_tick or a signed tick_offset, never both.

        Offsets shift numeric ticks or the +offset of an anchor expression without
        resolving/detaching its anchor. position indexes the target after removal.
        With neither timing argument, reorder within the current tick.
        """
        index, sample = self._draft(sample_id, expected_revision)
        if to_tick is not None and tick_offset is not None:
            raise CommandError("Choose to_tick or tick_offset")
        target = to_tick if to_tick is not None else tick
        if tick_offset is not None:
            match = KEY.fullmatch(tick)
            if not match:
                raise CommandError("Cannot offset an invalid tick key; specify to_tick")
            base = int(tick) if tick.isdigit() else int(match[2] or 0)
            shifted = base + tick_offset
            if shifted < 0:
                raise CommandError("Tick/anchor offset cannot become negative")
            target = (
                str(shifted) if tick.isdigit() else f"{{N_TICK:{match[1]}}}+{shifted}"
            )
        event = self._remove(sample, tick, event_index)
        address = self._insert(sample, target, event, position)
        return {**self._commit(sample, index, validate), **address}

    @command(write=True)
    def duplicate_event(
        self,
        sample_id: str,
        tick: str,
        event_index: Nonnegative,
        to_tick: str,
        position: Nonnegative | None = None,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Copy an event to a target tick. Anchor IDs/content are copied verbatim; repair duplicates."""
        index, sample = self._draft(sample_id, expected_revision)
        address = self._insert(
            sample, to_tick, self._event(sample, tick, event_index), position
        )
        return {**self._commit(sample, index, validate), **address}

    @command(write=True, destructive=True)
    def patch_event_content(
        self,
        sample_id: str,
        tick: str,
        event_index: Nonnegative,
        content: str | None = None,
        segments: list[dict] | None = None,
        patches: list[TextPatch] | None = None,
        segment_index: Nonnegative | None = None,
        expected_text: str | None = None,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Supply exactly one of content, segments or patches to edit event content.

        patches use nonoverlapping [start,end) Unicode character offsets in the original
        text; an empty range inserts. segment_index targets one segment's text for
        patches. expected_text guards a patch against stale source. Replacing content
        removes segments and vice versa. Loss and other settings remain intact.
        """
        index, sample = self._draft(sample_id, expected_revision)
        event = self._event(sample, tick, event_index)
        if not isinstance(event, dict) or event.get("op") == "open":
            raise CommandError("Only content events can be patched")
        if sum(v is not None for v in (content, segments, patches)) != 1:
            raise CommandError("Supply exactly one of content, segments or patches")
        if (segment_index is not None or expected_text is not None) and patches is None:
            raise CommandError("segment_index and expected_text apply only to patches")
        if content is not None:
            event.pop("segments", None)
            event["content"] = content
        elif segments is not None:
            event.pop("content", None)
            event["segments"] = copy.deepcopy(segments)
        else:
            target, field = event, "content"
            if segment_index is not None:
                items = event.get("segments")
                if not isinstance(items, list) or segment_index >= len(items):
                    raise CommandError("Segment not found", "not_found")
                target, field = items[segment_index], "text"
            if not isinstance(target, dict) or not isinstance(target.get(field), str):
                raise CommandError(
                    "Target must contain text; specify segment_index for segmented content"
                )
            text = target[field]
            if expected_text is not None and expected_text != text:
                raise CommandError("Content changed; read the event again", "conflict")
            ordered = sorted(patches, key=lambda p: (p.start, p.end))
            cursor, parts = 0, []
            for patch in ordered:
                if not cursor <= patch.start <= patch.end <= len(text):
                    raise CommandError(
                        "Patch ranges overlap or fall outside the original text"
                    )
                parts.extend((text[cursor : patch.start], patch.text))
                cursor = patch.end
            parts.append(text[cursor:])
            target[field] = "".join(parts)
        return self._commit(sample, index, validate)

    @command(write=True, destructive=True)
    def delete_event(
        self,
        sample_id: str,
        tick: str,
        event_index: Nonnegative,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Delete one event and prune its empty tick group. Drafts may temporarily be invalid."""
        index, sample = self._draft(sample_id, expected_revision)
        self._remove(sample, tick, event_index)
        return self._commit(sample, index, validate)

    @command(write=True, destructive=True)
    def rename_window(
        self,
        sample_id: str,
        window: str,
        new_window: str,
        validate: bool = False,
        expected_revision: Revision = None,
    ):
        """Rename a window across every event atomically; reject collisions with an existing window.

        Structured {WINDOW:id} references in content/segments follow the rename.
        Other free-form content, escaped references and metadata are not rewritten.
        """
        index, sample = self._draft(sample_id, expected_revision)
        if not new_window or len(new_window) > 128:
            raise CommandError("new_window must have 1..128 characters")
        events = [
            e
            for group in self._replay(sample).values()
            if isinstance(group, list)
            for e in group
            if isinstance(e, dict)
        ]
        if window != new_window and any(e.get("window") == new_window for e in events):
            raise CommandError("Target window already exists", "conflict")
        chosen = [e for e in events if e.get("window") == window]
        if not chosen:
            raise CommandError("Window not found", "not_found")
        for event in chosen:
            event["window"] = new_window
        reference = re.compile(r"(?<!\\)\{WINDOW:" + re.escape(window) + r"\}")
        for event in events:
            parts = [event] + (
                event.get("segments", []) if isinstance(event.get("segments"), list) else []
            )
            for part in parts:
                if isinstance(part, dict):
                    for field in ("content", "text"):
                        if isinstance(part.get(field), str):
                            part[field] = reference.sub(
                                lambda _: "{WINDOW:" + new_window + "}", part[field]
                            )
        return {**self._commit(sample, index, validate), "events_changed": len(chosen)}

    @command()
    async def validate_trajectory(self, sample_id: str, semantic: bool = False):
        """Compile a sample and report errors/warnings. semantic=True also calls configured LLM validators.

        No provider calls occur by default. Compiler errors return valid=false with a
        diagnostic path; the compiler stops at its first error. Warnings do not fail validation.
        """
        revision = self.state["revision"]
        try:
            plan = self._plan(sample_id)
        except CompileError as error:
            return {
                "valid": False,
                "diagnostics": [error.diagnostic()],
                "revision": revision,
            }
        result = {
            "valid": True,
            "diagnostics": plan["diagnostics"],
            "max_tick": plan["max_tick"],
            "row_count": len(plan["rows"]),
            "revision": revision,
        }
        if semantic:
            sample = copy.deepcopy(self._find(sample_id)[1])
            builder = SampleBuilder(
                self.compiler, copy.deepcopy(self.state["config"]), self.client
            )
            result["semantic"] = await builder.validators(sample)
            result["valid"] = result["semantic"]["valid"]
        return result

    @command()
    async def get_diagnostics(
        self, sample_id: str, severity: Literal["all", "error", "warning"] = "all"
    ):
        """View current compiler errors/warnings without contacting LLM endpoints."""
        result = await self.validate_trajectory(sample_id)
        if severity != "all":
            result["diagnostics"] = [
                d for d in result["diagnostics"] if d["severity"] == severity
            ]
        return result

    @command()
    def get_anchors(self, sample_id: str):
        """View resolved anchor ticks, capture phases and owning event paths/windows."""
        return {
            "anchors": self._plan(sample_id)["anchors"],
            "revision": self.state["revision"],
        }

    @command()
    def get_window_history(
        self,
        sample_id: str,
        window: str,
        start_tick: Nonnegative = 0,
        end_tick: Nonnegative | None = None,
        include_noops: bool = False,
        offset: Nonnegative = 0,
        limit: PageSize = 1000,
    ):
        """View a window's lifecycle, cumulative text through end_tick, events and paged token rows.

        Tick bounds are inclusive. Text includes the prefix before start_tick. NOOP rows
        are omitted by default; token rows retain phase, durability and loss information.
        """
        plan = self._plan(sample_id)
        end_tick = self._range(plan, start_tick, end_tick)
        life = next((w for w in plan["windows"] if w["id"] == window), None)
        if life is None:
            raise CommandError("Window not found", "not_found")
        rows = [
            r
            for r in plan["rows"]
            if r["window"] == window
            and start_tick <= r["tick"] <= end_tick
            and (include_noops or r["op"] != "noop")
        ]
        return {
            "revision": self.state["revision"],
            "window": life,
            "start_tick": start_tick,
            "end_tick": end_tick,
            "text": self.compiler.frame(plan, end_tick)["windows"][window]["text"],
            "events": [
                e
                for e in plan["events"]
                if e["window"] == window
                and e["start"] <= end_tick
                and e["end"] >= start_tick
            ],
            "rows": rows[offset : offset + limit],
            "total_rows": len(rows),
        }

    @command()
    def preview_snapshots(
        self,
        sample_id: str,
        start_tick: Nonnegative,
        end_tick: Nonnegative,
        step: Annotated[int, Field(ge=1)] = 1,
        windows: list[str] | None = None,
    ):
        """Preview post-commit snapshots over an inclusive timeline range, retaining earlier context.

        At most 100 frames, 50,000 compiled rows and 4 MB of output per request.
        Filter windows or narrow the range for larger trajectories. kv_tokens is zero
        for closed/pending windows; cached_tokens counts historical durable tokens.
        """
        plan = self._plan(sample_id)
        self._range(plan, start_tick, end_tick)
        ticks = range(start_tick, end_tick + 1, step)
        if len(ticks) > 100 or len(plan["rows"]) > 50_000:
            raise CommandError(
                "Preview exceeds 100 frames or 50,000 rows; narrow the range or split the sample"
            )
        known = {w["id"] for w in plan["windows"]}
        if windows is not None and not set(windows) <= known:
            raise CommandError("Unknown preview window", "not_found")
        frames, size = [], 0
        for tick in ticks:
            frame = self.compiler.frame(plan, tick)
            frame["windows"] = {
                w: data
                for w, data in frame["windows"].items()
                if windows is None or w in windows
            }
            for life in plan["windows"]:
                if life["id"] not in frame["windows"]:
                    continue
                data = frame["windows"][life["id"]]
                status = (
                    "pending"
                    if life["open"] > tick
                    else (
                        "closed"
                        if life["close"] is not None and life["close"] <= tick
                        else "open"
                    )
                )
                data.update(
                    status=status,
                    kv_tokens=data["cached_tokens"] if status == "open" else 0,
                )
            size += len(json.dumps(frame, ensure_ascii=False).encode())
            if size > 4_000_000:
                raise CommandError(
                    "Preview exceeds 4 MB; narrow the range or filter windows"
                )
            frames.append(frame)
        return {
            "revision": self.state["revision"],
            "max_tick": plan["max_tick"],
            "frames": frames,
        }

    @command(write=True, destructive=True)
    async def apply_batch(
        self,
        edits: Annotated[list[BatchEdit], Field(min_length=1, max_length=100)],
        validate: bool = True,
        dry_run: bool = False,
        expected_revision: Revision = None,
    ):
        """Apply up to 100 trajectory/event edits atomically as one undoable action.

        edits contains {name, arguments} using authoring command names. Nested batches,
        tool calls, saves and history operations are excluded. Per-edit expected_revision
        is not allowed; the outer revision guards the transaction. validate checks all
        changed surviving samples at the end; per-edit validate can still be requested.
        dry_run returns the prospective JSON Patch against the sample array without committing.
        """
        self._editable(expected_revision)
        before = self.state["samples"]
        working = {
            "samples": before.copy() if isinstance(before, SQLiteSamples) else copy.deepcopy(before),
            "config": self.state["config"], "revision": self.state["revision"],
        }
        if isinstance(working["samples"], SampleDraft):
            working["samples"].revision = self.edit_revision.get()
        worker = TrajectoryCommands(
            working,
            self.compiler,
            self.runner,
            self.client,
            history=SampleHistory(working, max_entries=0),
        )
        results = []
        for position, edit in enumerate(edits):
            if "expected_revision" in edit.arguments:
                raise CommandError(
                    "Use expected_revision on the batch, not its individual edits"
                )
            try:
                result = await worker.call(edit.name, edit.arguments)
            except CommandError as error:
                raise CommandError(
                    f"Batch edit {position} ({edit.name}): {error}", error.code
                ) from error
            result.pop("revision", None)
            results.append(result)
        after = working["samples"]
        if isinstance(after, SampleDraft):
            if validate:
                for sample in after.changed_samples():
                    self.compiler.compile(sample)
            changes = copy.deepcopy(after.operations)
        else:
            if validate:
                originals = {s["id"]: s for s in before if isinstance(s.get("id"), str)}
                for sample in after:
                    ident = sample.get("id")
                    unchanged = json_equal(sample, originals.get(ident)) if isinstance(ident, str) else any(json_equal(sample, old) for old in before)
                    if not unchanged:
                        self.compiler.compile(sample)
            changes = json_changes(before, after)
        revision = self.edit_revision.get()
        if not dry_run:
            revision = self.history.commit(after, f"Batch: {len(edits)} edits")
        return {
            "revision": revision,
            "results": results,
            "changes": changes,
            "dry_run": dry_run,
            "saved": not dry_run and hasattr(self.state, "store"),
        }

    @command()
    def diff_trajectories(
        self,
        sample_id: str,
        other_sample_id: str | None = None,
        other_sample: dict | None = None,
        ignore_id: bool = False,
        include_unified: bool = False,
        offset: Nonnegative = 0,
        limit: PageSize = 100,
    ):
        """Compare source JSON with another trajectory or supplied candidate, including invalid drafts.

        Supply exactly one of other_sample_id or other_sample. Returns paged RFC 6902
        operations transforming this sample into the other. ignore_id excludes IDs.
        Optional unified JSON text diff is capped at 200,000 characters.
        """
        if (other_sample_id is None) == (other_sample is None):
            raise CommandError("Supply exactly one of other_sample_id or other_sample")
        before = copy.deepcopy(self._find(sample_id)[1])
        after = copy.deepcopy(
            self._find(other_sample_id)[1]
            if other_sample_id is not None
            else other_sample
        )
        if ignore_id:
            before.pop("id", None)
            after.pop("id", None)
            before.pop("uuid", None)
            after.pop("uuid", None)
        changes = json_changes(before, after)
        result = {
            "revision": self.state["revision"],
            "equal": not changes,
            "changes": changes[offset : offset + limit],
            "total_changes": len(changes),
        }
        if include_unified:
            lines = difflib.unified_diff(
                json.dumps(
                    before, sort_keys=True, indent=2, ensure_ascii=False
                ).splitlines(keepends=True),
                json.dumps(
                    after, sort_keys=True, indent=2, ensure_ascii=False
                ).splitlines(keepends=True),
                fromfile=sample_id,
                tofile=other_sample_id or "candidate",
            )
            parts, remaining = [], 200_000
            result["unified_truncated"] = False
            for line in lines:
                parts.append(line[:remaining])
                remaining -= len(line)
                if remaining < 0:
                    result["unified_truncated"] = True
                    break
            result["unified_diff"] = "".join(parts)
        return result

    @command()
    def get_edit_history(self, include_changes: bool = False):
        """View available undo/redo actions for source samples across MCP, browser and generation edits.

        Newest/next actions appear first. History is session-only, bounded to 50 actions
        and 32 MB; configuration and simulated tool sessions are excluded.
        include_changes includes each action's forward JSON Patch against the sample array.
        """
        return self.history.view(include_changes)

    @command(write=True, destructive=True)
    def undo_edits(
        self,
        steps: Annotated[int, Field(ge=1, le=50)] = 1,
        expected_revision: Revision = None,
    ):
        """Undo source edits as one revision change. Stop generation first; the undo commits immediately."""
        self._editable(expected_revision)
        return self.history.restore("undo", steps)

    @command(write=True, destructive=True)
    def redo_edits(
        self,
        steps: Annotated[int, Field(ge=1, le=50)] = 1,
        expected_revision: Revision = None,
    ):
        """Reapply undone source edits. A new source edit discards the redo branch."""
        self._editable(expected_revision)
        return self.history.restore("redo", steps)

    @command()
    def list_simulated_tools(self, session_id: str | None = None):
        """Discover the real Remix simulated-tool schemas, including clock, RAM files, memories, notes and skills.

        Dispatch with call_simulated_tool. subagent is available only when a session
        explicitly selects a configured endpoint. Even read calls advance the clock.
        """
        return self.tool_sessions.definitions(session_id)

    @command(write=True)
    def create_tool_call(
        self,
        sample_id: str,
        tick: str,
        tool: str,
        arguments: dict,
        duration_ticks: Nonnegative = 0,
        window: str | None = None,
        outcome: dict | None = None,
        position: Nonnegative | None = None,
        expected_revision: Revision = None,
        delivery: Literal["instant", "stream"] = "instant",
        content: str | None = None,
        segments: list[dict] | None = None,
        loss: dict | None = None,
    ):
        """Schedule an executable synthetic tool event and compile before committing one undoable edit.

        Tool windows exist from tick 0; request and masked result share that window.
        Instant delivery dispatches at tick; stream generates one request token per
        tick and dispatches on the next tick after the last token. Optional content
        or segments replace the canonical <tool> request and support anchors/NOOPs.
        Sandbox changes at dispatch + duration_ticks. Only subagent requires a
        recorded outcome; replay never contacts an endpoint. Save with save_dataset.
        """
        index, sample = self._draft(sample_id, expected_revision)
        event = {
            "op": "tool-call",
            "tool": tool,
            "arguments": arguments,
            "window": (
                window if window is not None else f"tool-result-{uuid.uuid4().hex[:12]}"
            ),
            "duration-ticks": duration_ticks,
            "delivery": delivery,
        }
        for key, value in (("content", content), ("segments", segments), ("loss", loss)):
            if value is not None:
                event[key] = value
        if outcome is not None:
            event["outcome"] = outcome
        address = self._insert(sample, tick, event, position)
        plan = self.compiler.compile(sample)
        path = f"replay[{address['tick']}][{address['event_index']}]"
        call = next(c for c in plan["tool_calls"] if c["path"] == path)
        return {**self._commit(sample, index), **address, "tool_call": call}

    @command()
    def get_sandbox_state(
        self, sample_id: str, tick: Nonnegative, include_calls: bool = True
    ):
        """Inspect historical virtual files, memory, notes, skills, clock and tool statuses.

        Post-commit state at tick; running calls omit future results. Reading never
        executes external requests or mutates a workbench. Set include_calls=False
        to omit argument/outcome logs from a large preview. Maximum output: 4 MB.
        """
        plan = self._plan(sample_id)
        sandbox = self.compiler.frame(plan, tick)["sandbox"]
        if not include_calls:
            sandbox.pop("tool_calls")
        if len(json.dumps(sandbox, ensure_ascii=False).encode()) > 4_000_000:
            raise CommandError("Sandbox preview exceeds 4 MB; set include_calls=False")
        return {
            "sample_id": sample_id,
            "tick": tick,
            "sandbox": sandbox,
            "revision": self.state["revision"],
        }

    @command(write=True)
    def create_tool_session(
        self,
        session_id: str | None = None,
        clock_seed: str | None = None,
        seconds_per_call: (
            Annotated[float, Field(ge=0, le=31_536_000, allow_inf_nan=False)] | None
        ) = None,
        max_calls: Annotated[int, Field(ge=1, le=256)] | None = None,
        max_bytes: Annotated[int, Field(ge=128, le=2_000_000)] = 2_000_000,
        subagent_endpoint: str | None = None,
    ):
        """Create an isolated simulated-tool workbench (up to eight sessions).

        Defaults use the configured synthetic clock and call budget. Sessions are RAM-only,
        separate from generation sandboxes, and excluded from saves/source edit history.
        subagent_endpoint optionally enables bounded text-only requests to that configured
        endpoint; no parent RAM state or trajectory context is sent to it.
        """
        clock = self.state["config"].get("clock", {})
        options = {
            "seed": (
                clock_seed
                if clock_seed is not None
                else clock.get("seed", "2030-01-01T09:00:00+00:00")
            ),
            "seconds_per_call": (
                seconds_per_call
                if seconds_per_call is not None
                else clock.get("seconds_per_call", 1)
            ),
            "max_calls": (
                max_calls
                if max_calls is not None
                else self.state["config"].get("max_tool_calls", 64)
            ),
            "max_bytes": max_bytes,
        }
        if subagent_endpoint is not None:
            if not any(
                e["id"] == subagent_endpoint for e in self.state["config"]["endpoints"]
            ):
                raise CommandError(
                    "Configured subagent endpoint not found", "not_found"
                )

            async def broker(task, depth):
                endpoint = next(
                    (
                        e
                        for e in self.state["config"]["endpoints"]
                        if e["id"] == subagent_endpoint
                    ),
                    None,
                )
                if endpoint is None:
                    raise ToolError("Configured subagent endpoint no longer exists")
                try:
                    message = await (self.client or LLMClient()).complete(
                        copy.deepcopy(endpoint),
                        [
                            {
                                "role": "system",
                                "content": "Complete this delegated task. No tools or parent workspace are available.",
                            },
                            {"role": "user", "content": task},
                        ],
                        tools=None,
                    )
                except Exception as error:
                    raise ToolError("Configured subagent request failed") from error
                text = message.get("content") if isinstance(message, dict) else None
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or len(text) > 200_000
                    or message.get("tool_calls")
                ):
                    raise ToolError(
                        "Subagent must return nonempty text of at most 200,000 characters without tool calls"
                    )
                return text

            options["broker"] = broker
        return self.tool_sessions.create(session_id, options, subagent_endpoint)

    @command()
    def list_tool_sessions(self):
        """List live simulated-tool workbenches with quotas, clock and state counts."""
        return {
            "sessions": [
                self.tool_sessions.inspect(ident, False, False)
                for ident in self.tool_sessions.sessions
            ],
            "max_sessions": self.tool_sessions.max_sessions,
        }

    @command()
    def get_tool_session(
        self, session_id: str, include_state: bool = True, include_log: bool = True
    ):
        """Inspect virtual files, memory, notes, skills and the bounded actual-call log.

        Inspection does not consume a simulated call or advance time. Endpoint credentials
        are excluded. Interaction IDs can be used with record_tool_interaction.
        """
        return self.tool_sessions.inspect(session_id, include_state, include_log)

    @command(write=True, destructive=True, open_world=True)
    async def call_simulated_tool(
        self,
        session_id: str,
        name: str,
        arguments: dict,
        record_sample_id: str | None = None,
        expected_revision: Revision = None,
    ):
        """Execute a simulated Remix tool in a session, preserving its real outcome and state.

        Discover exact name/arguments with list_simulated_tools. Valid dispatched failures
        return outcome.ok=false and consume clock/budget. Invalid arguments do not execute.
        record_sample_id also appends the actual call/result in one tick-0 tool window
        as one undoable source edit.
        Recording requires a compilable sample (or empty draft) and stopped generation.
        A rejected recording leaves the virtual state/log unchanged. Configured subagent
        requests can contact an LLM; their external request cannot be rolled back.
        """
        if expected_revision is not None and record_sample_id is None:
            raise CommandError(
                "expected_revision applies to recording; supply record_sample_id"
            )
        recorder = None
        context = None
        if record_sample_id is not None:
            self._editable(expected_revision)
            _, sample = self._find(record_sample_id)
            if name == "chat":
                windows = (
                    self.compiler.compile(sample)["windows"] if sample.get("replay") else []
                )
                context = chat_context(windows, sample.get("metadata"))
            recorder = lambda entry: self._record_interaction(
                record_sample_id, entry, expected_revision
            )
        return await self.tool_sessions.call(
            session_id, name, arguments, recorder, context
        )

    def _record_interaction(self, sample_id, entry, expected_revision):
        index, sample = self._draft(sample_id, expected_revision)
        replay = self._replay(sample)
        events = [
            e for group in replay.values() if isinstance(group, list) for e in group
        ]
        call_window = result_window = f"tool-result-{entry['id']}"
        if any(
            isinstance(e, dict)
            and e.get("window") in (f"tool-call-{entry['id']}", result_window)
            for e in events
        ):
            raise CommandError(
                "This interaction is already recorded in the trajectory", "conflict"
            )
        builder = SampleBuilder(self.compiler, self.state["config"], self.client)
        builder.sample = sample
        builder.next_tick = (
            self.compiler.compile(sample)["max_tick"] + 1 if events else 0
        )
        start_tick = builder.next_tick
        request_id = "remix-mcp-" + entry["id"]

        replay.setdefault("0", []).append(
            {"op": "open", "window": result_window, "window-type": "tool"}
        )
        builder.add_stream(
            call_window,
            tool_request(
                entry["name"], entry["arguments"], self.compiler.special.values()
            ).replace("{N_TICK", "\\u007bN_TICK").replace("{WINDOW", "\\u007bWINDOW"),
            close=False,
            request_id=request_id,
        )
        builder.add_stream(
            result_window,
            tool_message(
                "tool_result", entry["outcome"], self.compiler.special.values()
            ).replace("{N_TICK", "\\u007bN_TICK").replace("{WINDOW", "\\u007bWINDOW"),
            op="append",
            source="tool",
            close=False,
            loss={"mask": "exclude"},
            request_id=request_id,
        )
        if entry["name"] == "chat" and entry["outcome"]["ok"]:
            plan = self.compiler.compile(builder.sample)
            if not chat_context(plan["windows"], sample.get("metadata"))["chat_open"]:
                raise CommandError('No window named "chat" is open')
            builder.add_stream(
                "chat",
                tool_text(
                    entry["outcome"]["result"]["content"], self.compiler.special.values()
                ).replace("{N_TICK", "\\{N_TICK").replace("{WINDOW", "\\{WINDOW"),
                op="append",
                source="tool",
                loss={"mask": "exclude"},
                request_id=request_id,
            )
        return {
            **self._commit(builder.sample, index),
            "interaction_id": entry["id"],
            "request_id": request_id,
            "call_window": call_window,
            "result_window": result_window,
            "start_tick": start_tick,
        }

    @command(write=True, destructive=True)
    def record_tool_interaction(
        self,
        sample_id: str,
        session_id: str,
        interaction_id: str,
        expected_revision: Revision = None,
    ):
        """Append a previous actual simulated call/result to a trajectory without executing it again.

        Uses supervised <tool> text and masked <tool_result> text in one tick-0 window.
        Repeated recording of the same interaction in one trajectory is rejected.
        """
        entry = self.tool_sessions.interaction(session_id, interaction_id)
        return self._record_interaction(sample_id, entry, expected_revision)

    @command(write=True, destructive=True)
    def reset_tool_session(self, session_id: str):
        """Clear a workbench's RAM state/log and restore its initial clock and call budget."""
        return self.tool_sessions.reset(session_id)

    @command(write=True, destructive=True)
    def delete_tool_session(self, session_id: str):
        """Destroy one simulated-tool workbench and its RAM state/log."""
        return self.tool_sessions.delete(session_id)

    @command(write=True)
    async def save_dataset(self):
        """Report a checkpoint of the SQLite dataset; source edits commit immediately."""
        revision = await self.runner.save()
        return {"saved": True, "revision": revision}
