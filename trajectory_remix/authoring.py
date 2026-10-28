"""Typed trajectory commands shared by the local API and the MCP bridge.

Edits operate on copies and commit once. Invalid drafts can be repaired across
several commands; validate=True makes a single edit conditional on compilation.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import inspect
import json
import re
import uuid
import jsonschema
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .changes import HistoryError, SampleHistory, json_changes, json_equal
from .compiler import ANCHOR, CompileError, valid_tick, shift_tick
from .dataset import evaluation_source, new_sample, prepare_sample
from .llm import SampleBuilder
from .metadata import MetadataFilter
from .metadata import counts as metadata_counts
from .metadata import query as query_metadata
from .preview import PreviewCache
from .review import EDIT_SCHEMA, apply_edits, checklist_report
from .trajectory_review import (
    ChecklistCheck, ScenarioChecklist, build_checklist, fingerprint, public_checklist, result_schema,
)
from .guidance import GUIDE_TOPICS, GUIDE_TITLES, checklist_markdown, custom_documents
from .search import SearchQuery
from .storage import SampleDraft, SQLiteSamples, StorageConflict
from .tool_sessions import ToolSessionError, ToolSessions
from .tools import (
    WindowProperties,
    tool_message,
    tool_request,
    tool_text,
)

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
        self.tool_sessions = ToolSessions(registry=compiler.tools)
        self.previews = PreviewCache(compiler)
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
        if getattr(self.runner, "tokenizer_migration", None) and self.runner.tokenizer_migration.running:
            raise CommandError("Wait for tokenizer migration to finish", "busy")
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

    def _editable(self, expected_revision, sample_id=None):
        if getattr(self.runner, "tokenizer_migration", None) and self.runner.tokenizer_migration.running:
            raise CommandError("Wait for tokenizer migration to finish", "busy")
        if self.runner.status["running"] or (getattr(self.runner, "scenario_runner", None) and self.runner.scenario_runner.locked(sample_id)):
            raise CommandError("Stop generation before editing the dataset", "busy")
        if (
            expected_revision is not None
            and expected_revision != self.state["revision"]
        ):
            raise CommandError(
                "Dataset changed; read it again before editing", "conflict"
            )

    def _commit(self, sample, index=None, validate=False, *, review=False):
        self._editable(None, self.state["samples"][index]["id"] if index is not None else sample.get("id"))
        from .dataset import invalidate_automation
        # An explicit sign-off changes review metadata, not the reviewed source.
        # Ordinary edits must keep invalidating assessments of older content.
        if index is not None and not review:
            sample = invalidate_automation(copy.deepcopy(sample), self.state["samples"][index])
        sample = prepare_sample(
            sample, self.state["samples"][index] if index is not None else None
        )
        self.compiler.slots_for(sample)
        ident = sample.get("id")
        if not isinstance(ident, str) or not ident:
            raise CommandError("A nonempty sample id is required")
        samples = self.state["samples"]
        if isinstance(samples, (SQLiteSamples, SampleDraft)):
            found = samples.find(ident)
            duplicate = found is not None and found[0] != index
        else:
            duplicate = any(
                i != index and other.get("id") == ident
                for i, other in enumerate(samples)
            )
        if duplicate:
            raise CommandError(f"Duplicate sample ID: {ident}", "conflict")
        # Hidden arguments are never valid model calls, even in an unfinished draft.
        from .tools.runtime import reject_invisible
        replay = sample.get("replay", {})
        if isinstance(replay, dict):
            for group in replay.values():
                if not isinstance(group, list):
                    continue
                for event in group:
                    if isinstance(event, dict) and event.get("op") == "tool-call" and event.get("tool") in self.compiler.tools.tools:
                        try:
                            tool = self.compiler.tools.check_visibility(event["tool"])
                            reject_invisible(event.get("arguments", {}), tool.schema)
                        except ValueError as error:
                            raise CommandError(str(error)) from error
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
        return {
            "sample_id": ident,
            "revision": revision,
            "saved": hasattr(self.state, "store"),
        }

    def _draft(self, sample_id, expected_revision):
        self._editable(expected_revision, sample_id)
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
        if not valid_tick(tick):
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
        return self._preview(sample_id).plan

    def _preview(self, sample_id):
        _, sample = self._find(sample_id)
        return self.previews.compile(sample, revision=self.state["revision"])

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
            "guidance": {"tool": "get_guidance", "instruction": "Read dataset and scenario guidance before creating, validating or repairing trajectories."},
        }

    @command()
    def get_authoring_guide(
        self,
        topic: Literal[
            "start", "first", "parallel", "edit", "sandbox", "tools", "search", "review", "reference", "example"
        ] = "start",
        scenario: str | None = None,
    ):
        """Read a short authoring lesson, complete reference, or example trajectory.

        Topics: first (ticks/windows), parallel (overlapping work), edit (small repairs),
        sandbox (trajectory files), tools (starter tool selection), search (query syntax),
        review (semantic review/context dependencies; read before reviewing or repairing), reference (full manual),
        example (full build-graph JSON trajectory). Also returns current dataset custom
        guidance; pass scenario for its additional requirements. get_guidance exposes
        every guide and checklist in the editor. No source or sandbox changes.
        """
        root = Path(__file__).parent
        if topic == "reference":
            path = root / "Skill.md"
        elif topic == "example":
            path = root / "build-graph-analysis.json"
        else:
            path = root / "guides" / f"{topic}.md"
        return {"content": path.read_text(encoding="utf-8"),
                "custom_guidance": custom_documents(self.state["config"], scenario),
                "guidance_tool": "get_guidance"}

    @command()
    def list_trajectories(
        self, query: str = "", offset: Nonnegative = 0, limit: PageSize = 50
    ):
        """Search readable trajectory text, names, properties and tags, then paginate.

        For "review five candidate trajectories", call with query="@state:candidate",
        limit=5. Add @batch:<batch-id> to restrict a batch. Filter here before
        fetching replay content; do not enumerate all trajectories to check states.

        Case-insensitive query syntax: spaces AND, | OR, - negation, (groups),
        * word wildcards, #tag, @name:glob, @uuid:glob, @windows:>5,
        @uses_tool:glob, @state:candidate, @batch:glob, @errors:true/false,
        @warnings:true/false. AND binds tighter than OR;
        - applies to a term or group.
        Example: python -(math complex) #coding. Empty query lists everything.
        Read get_authoring_guide(topic="search") for the full syntax and examples.
        """
        if isinstance(self.state["samples"], SQLiteSamples):
            revision = self.state["revision"]
            total, trajectories = self.state["samples"].store.listing(
                query, offset, limit
            )
            return {"revision": revision, "total": total, "trajectories": trajectories}
        search = SearchQuery(query)
        samples = [s for s in self.state["samples"] if search.matches(s, self.compiler)]
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
        self,
        filters: list[MetadataFilter] | None = None,
        tags: list[str] | None = None,
        tag_mode: Literal["all", "any"] = "all",
        offset: Nonnegative = 0,
        limit: PageSize = 100,
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
        self,
        key: str = "tags",
        filters: list[MetadataFilter] | None = None,
        tags: list[str] | None = None,
        tag_mode: Literal["all", "any"] = "all",
        limit: PageSize = 100,
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
        """Replace the complete source JSON. validate=True rejects invalid changes atomically.

        This operation is intended only for large-scale or structural edits where incremental
        changes are impractical. In most cases, incremental edits using create_event, create_tool_call,
        and apply_batch are preferred.
        """
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
        """Insert an open, append, generate, show_tools, tool-call or invisible-tool-call event at a numeric or anchor tick key.

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
            try:
                target = shift_tick(tick, tick_offset)
            except ValueError as error:
                raise CommandError(str(error)) from error
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
                event.get("segments", [])
                if isinstance(event.get("segments"), list)
                else []
            )
            for part in parts:
                if isinstance(part, dict):
                    for field in ("content", "text"):
                        if isinstance(part.get(field), str):
                            part[field] = reference.sub(
                                lambda _: "{WINDOW:" + new_window + "}", part[field]
                            )
        return {**self._commit(sample, index, validate), "events_changed": len(chosen)}

    def _review_checklist(self, sample, *, allow_empty=False):
        metadata = sample.get("metadata", {})
        if not isinstance(metadata, dict):
            raise CommandError("Trajectory metadata must be an object to resolve its scenario")
        raw_tags = metadata.get("tags", [])
        if not isinstance(raw_tags, list) or any(not isinstance(t, str) for t in raw_tags):
            raise CommandError("metadata.tags must be an array of tag names")
        if metadata.get("scenario") is not None and not isinstance(metadata["scenario"], str):
            raise CommandError("metadata.scenario must be a scenario name")
        tags = {t.casefold() for t in raw_tags}
        scenario_runner = getattr(self.runner, "scenario_runner", None)
        scenarios = scenario_runner.registry.scenarios if scenario_runner else {}
        matches = [s for s in scenarios.values() if (s.flow.tag or s.name).casefold() in tags]
        if len(matches) > 1:
            families = {s.family for s in matches}
            if len(families) > 1:
                raise CommandError("Multiple scenario tags; select one scenario before reviewing", "scenario_conflict")
            # Variations intentionally share a domain tag. Explicit provenance
            # selects its exact contract; tag-only legacy samples use the root.
            selected = metadata.get('scenario')
            exact = [s for s in matches if s.name == selected] if selected else []
            if exact:
                matches = exact
            elif not selected or (metadata.get('review_checklist') and metadata.get('scenario_family') in families):
                matches = [s for s in matches if s.name in families]
            else:
                matches = []
            if not matches:
                raise CommandError('Scenario metadata disagrees with its family tag', 'scenario_conflict')
        snapshot = metadata.get("review_checklist")
        if snapshot is not None:
            try:
                checklist = ScenarioChecklist.model_validate(snapshot)
            except ValidationError as error:
                raise CommandError(f"Invalid saved review checklist: {error}") from error
            origin = "generation_snapshot"
            if matches and matches[0].name != checklist.scenario:
                same_family = (metadata.get('scenario_family') == matches[0].family
                               and metadata.get('scenario') == checklist.scenario
                               and (matches[0].flow.tag or matches[0].name).casefold() == checklist.tag.casefold())
                if not same_family:
                    raise CommandError("Scenario tag disagrees with the saved checklist", "scenario_conflict")
            if checklist.tag.casefold() not in tags and not metadata.get("scenario"):
                raise CommandError("Saved checklist has no matching scenario tag or metadata", "scenario_conflict")
        else:
            scenario = matches[0] if matches else scenarios.get(metadata.get("scenario"))
            custom_scenarios = self.state["config"].get("review_guidance", {}).get("scenarios", {})
            custom_scenario = metadata.get("scenario")
            if scenario is None and custom_scenario in custom_scenarios:
                checklist, origin = ScenarioChecklist(scenario=custom_scenario, tag=custom_scenario), "dataset_config"
            elif scenario is None and not custom_scenario and custom_documents(self.state["config"]):
                checklist, origin = ScenarioChecklist(scenario="dataset", tag="dataset"), "dataset_config"
            elif scenario is None:
                raise CommandError(
                    "No scenario checklist found. Configure --scenarios-dir and assign its scenario tag, or retain the generated review_checklist snapshot.",
                    "scenario_not_found",
                )
            else:
                checklist, origin = build_checklist(scenario), "scenario_registry"
        if metadata.get("scenario") and metadata["scenario"] != checklist.scenario:
            raise CommandError("Scenario metadata disagrees with its tag/checklist", "scenario_conflict")
        checklist = self._with_custom_guidance(checklist, include_scenario=origin != "dataset_config" or bool(metadata.get("scenario")))
        if not checklist.checks and not allow_empty:
            raise CommandError("This scenario defines no review checks", "checklist_not_found")
        return checklist, origin

    def _with_custom_guidance(self, checklist, *, include_scenario=True):
        checklist = checklist.model_copy(deep=True)
        for scope in ("dataset", "scenario"):
            documents = [d for d in custom_documents(self.state["config"], checklist.scenario if include_scenario else None)
                         if d["id"].startswith(f"custom.{scope}.")]
            if documents:
                checklist.checks[f"$custom.{scope}"] = ChecklistCheck(prompt="\n\n".join(
                    f"# {d['title']}\n\n{d['content']}" for d in documents))
        return checklist

    @command()
    def get_guidance(self, sample_id: str | None = None, scenario: str | None = None):
        """Read all guides available to MCP, also shown in View > Trajectory Guidelines.

        Read before creating, validating or repairing. Without a sample, select a
        scenario to see creation prompts, review/repair guides and its checklist.
        With sample_id, use its original saved checklist plus current custom guidance.
        Dataset and scenario requirements supplement the read-only standard guides.
        Custom checks require their own valid/feedback results in record_trajectory_review.
        No model calls, credentials or dataset changes.
        """
        if sample_id is not None and scenario is not None:
            raise CommandError("Select either sample_id or scenario")
        scenario_runner = getattr(self.runner, "scenario_runner", None)
        scenarios = scenario_runner.registry.scenarios if scenario_runner else {}
        names = sorted(set(scenarios) | set(self.state["config"].get("review_guidance", {}).get("scenarios", {})))
        checklist, origin = None, None
        if sample_id is not None:
            sample = self._find(sample_id)[1]
            try:
                checklist, origin = self._review_checklist(sample, allow_empty=True)
                scenario = checklist.scenario if origin != "dataset_config" or sample.get("metadata", {}).get("scenario") else None
            except CommandError as error:
                if error.code != "scenario_not_found":
                    raise
        elif scenario is not None:
            if scenario not in names:
                raise CommandError(f"Unknown guidance scenario: {scenario}", "not_found")
            if scenario in scenarios:
                checklist, origin = self._with_custom_guidance(build_checklist(scenarios[scenario])), "scenario_registry"
            else:
                checklist, origin = self._with_custom_guidance(ScenarioChecklist(scenario=scenario, tag=scenario)), "dataset_config"
        elif custom_documents(self.state["config"]):
            checklist, origin = self._with_custom_guidance(ScenarioChecklist(scenario="dataset", tag="dataset"), include_scenario=False), "dataset_config"
        documents = []
        for topic, title in zip(GUIDE_TOPICS, GUIDE_TITLES):
            documents.append({"id": f"standard.{topic}", "title": title,
                              "content": self.get_authoring_guide(topic)["content"],
                              "format": "json" if topic == "example" else "markdown",
                              "source": "standard", "editable": False})
        documents.extend(custom_documents(self.state["config"], scenario, include_empty=True))
        if checklist is not None:
            documents.append({"id": "scenario.contract", "title": "Review Contract (JSON)",
                              "content": json.dumps(public_checklist(checklist, origin), indent=2, ensure_ascii=False),
                              "format": "json", "source": origin, "editable": False})
            documents.append({"id": "scenario.checklist", "title": "Review Checklist",
                              "content": checklist_markdown(checklist), "format": "markdown",
                              "source": origin, "editable": False})
            # Saved review/repair prompts remain visible even without the scenario files.
            for name, check in checklist.checks.items():
                if name.startswith("$custom."):
                    continue
                for kind, text in [("Review", check.prompt), ("Repair", check.repair_prompt),
                                   *[(f"Repair Guide {i}", g) for i, g in enumerate(check.repair_guides, 1)]]:
                    if text:
                        documents.append({"id": f"check.{name}.{kind}", "title": f"{name}: {kind}",
                                          "content": text, "format": "markdown", "source": origin, "editable": False})
        if scenario in scenarios:
            for filename, text in scenarios[scenario].prompts.items():
                documents.append({"id": f"scenario.prompt.{filename}", "title": filename,
                                  "content": text, "format": "markdown", "source": "scenario_registry", "editable": False})
        result = {"revision": self.state["revision"], "scenario": scenario, "scenarios": names, "documents": documents}
        if checklist is not None:
            result["checklist"] = public_checklist(checklist, origin)
        return result

    @command()
    def get_generation_analytics(self):
        """Compare retained candidates, inference costs, repairs and approvals by generation mode.

        Throughput uses recorded active generation elapsed time and excludes
        external review time. Unknown legacy counters are not reconstructed.
        """
        from .content_first import dataset_generation_metrics
        return {'modes': dataset_generation_metrics(self.state['samples'])}

    @command()
    def get_generation_record(self, sample_id: str, request_index: Nonnegative | None = None,
                              include_failures: bool = False):
        """Inspect original generation requirements, bounded requests and failed proposals.

        By default return request summaries. Select request_index for its full raw
        output/reasoning, including incomplete or schema-invalid text. Set
        include_failures for construction inputs and uncompiled proposed replay.
        Generation jobs are retained independently of the current active job.
        Repair the saved trajectory with existing replay editing tools, then review
        against get_review_checklist; this read never invokes an inference model.
        """
        _, sample = self._find(sample_id)
        metadata = sample.get('metadata', {})
        record = copy.deepcopy(metadata.get('generation_record', {}))
        if not record and metadata.get('generation_mode') != 'content-first':
            raise CommandError('No content-first generation record for this trajectory', 'not_found')
        job = metadata.get('generation_job')
        if job and hasattr(self.state, 'store'):
            with self.state.store.connect() as db:
                row = db.execute('SELECT snapshot FROM generation_jobs WHERE id=?', (job,)).fetchone()
            if row:
                saved = json.loads(row[0])
                from .content_first import summarize_requests
                requests = saved.get('content_requests', [])
                record.update(job=job, requests=requests,
                              failures=saved.get('failed_proposals', []),
                              requirements={'flow': saved['flow'], 'prompts': saved['prompts']},
                              metrics=summarize_requests(requests, saved.get('elapsed_seconds', 0), saved['status']['phase']))
        if not record:
            raise CommandError('No content-first generation record for this trajectory', 'not_found')
        requests = record.get('requests', [])
        if request_index is not None:
            if request_index >= len(requests):
                raise CommandError('Generation request index out of range')
            record['request'] = copy.deepcopy(requests[request_index])
        record['requests'] = [{k: v for k, v in request.items() if k not in ('raw_output', 'reasoning')} for request in requests]
        if not include_failures:
            record['failures'] = [{k: v for k, v in failure.items() if k not in ('sample', 'inputs')} for failure in record.get('failures', [])]
        return {'sample_id': sample_id, 'revision': self.state['revision'], **record}

    @command()
    def get_review_checklist(self, sample_id: str):
        """Fetch this trajectory's scenario checklist automatically from its tag/provenance.

        Generated snapshots preserve the original rules even without scenario files.
        Older/tag-only samples use the configured scenario registry. Conflicting or
        unknown scenarios fail explicitly. No model API calls or dataset changes.
        """
        sample = self._find(sample_id)[1]
        checklist, origin = self._review_checklist(sample)
        scenario = checklist.scenario if origin != "dataset_config" or sample.get("metadata", {}).get("scenario") else None
        return {"sample_id": sample_id, "revision": self.state["revision"],
                "checklist": public_checklist(checklist, origin),
                "custom_guidance": custom_documents(self.state["config"], scenario)}

    @command()
    def review_trajectory(self, sample_id: str, include_source: bool = True):
        """Prepare YOUR review with the scenario checklist, guide and compiler diagnostics.

        The checklist is fetched automatically; assess every rule yourself, using
        event/window tools for further evidence. No model API is called. For large
        samples set include_source=false and inspect bounded events/snapshots.
        After repairs reread this tool, then record_trajectory_review with its
        revision, checklist_hash and per-check results_schema. This tool does not
        approve a sample or interpret an old validator's verdict as your review.
        """
        sample = copy.deepcopy(self._find(sample_id)[1])
        result = self.get_review_checklist(sample_id)
        try:
            plan = self.compiler.compile(sample)
            compilation = {"valid": True, "diagnostics": plan["diagnostics"], "max_tick": plan["max_tick"]}
        except CompileError as error:
            compilation = {"valid": False, "diagnostics": [error.diagnostic()]}
        result.update(
            source_hash=hashlib.sha256(json.dumps(evaluation_source(sample), sort_keys=True).encode()).hexdigest(),
            compilation=compilation,
            guide=self.get_authoring_guide("review")["content"],
            guidance=self.get_guidance(sample_id=sample_id),
            context={k: sample.get("metadata", {}).get(k) for k in ("concept", "roles", "context_tags")},
        )
        if include_source:
            # Prior evaluations are not evidence for this independent review.
            result["sample"] = evaluation_source(sample)
        return result

    @command(write=True, destructive=True)
    def repair_trajectory(
        self, sample_id: str,
        edits: Annotated[list[dict], Field(min_length=1, max_length=32, json_schema_extra={"items": EDIT_SCHEMA["properties"]["edits"]["items"]})],
        expected_revision: Nonnegative, checklist_hash: str, dry_run: bool = False,
    ):
        """Apply targeted review repairs atomically; automatically return the scenario checklist.

        Read review_trajectory first. Use original replay[tick][index] event_ids.
        Actions: replace/delete, insert_before/insert_after, append at a tick, or
        replace_text with a JSON-pointer field and exact unique old/new strings.
        Repairs cannot alter identity/metadata. The whole candidate must compile;
        failure changes nothing. dry_run validates without saving. Saved repairs
        invalidate old assessments when source changes; review all rules again
        before recording review.
        No model/provider calls. Revision and checklist_hash bind the repair to
        the source and checklist you read, including after scenario changes.
        """
        index, sample = self._draft(sample_id, expected_revision)
        checklist, origin = self._review_checklist(sample)
        if checklist_hash != fingerprint(checklist):
            raise CommandError("Checklist changed; reread review_trajectory", "conflict")
        try:
            jsonschema.validate({"edits": edits}, EDIT_SCHEMA)
            candidate = apply_edits(sample, edits)
        except (ValueError, jsonschema.ValidationError) as error:
            raise CommandError(str(error)) from error
        plan = self.compiler.compile(candidate)
        result = {"sample_id": sample_id, "revision": self.state["revision"], "saved": False}
        if not dry_run:
            if candidate.get('metadata', {}).get('generation_mode') == 'content-first':
                candidate['metadata']['external_repairs'] = candidate['metadata'].get('external_repairs', 0) + 1
            result = self._commit(candidate, index)
        return {**result, "diagnostics": plan["diagnostics"], "review_required": True,
                "checklist": public_checklist(checklist, origin),
                "guidance": self.get_guidance(sample_id=sample_id)}

    @command(write=True)
    def record_trajectory_review(
        self, sample_id: str, results: dict,
        reviewer: Annotated[str, Field(min_length=1, max_length=200)],
        summary: Annotated[str, Field(min_length=1, max_length=10000)],
        expected_revision: Nonnegative, checklist_hash: str,
    ):
        """Record YOUR complete per-rule review at state 2/-2; no model calls.

        Read review_trajectory after the final repair. results must match its
        checklist.results_schema exactly, covering every category and rule for the
        entire trajectory. Missing/extra items, invalid applicability and unresolved
        context cannot approve a sample. Code computes the verdict. Evidence is
        your assessment, not independent verification. Never grants Ready (3).
        """
        index, sample = self._draft(sample_id, expected_revision)
        checklist, _ = self._review_checklist(sample)
        if checklist_hash != fingerprint(checklist):
            raise CommandError("Checklist changed; reread review_trajectory", "conflict")
        try:
            jsonschema.validate(results, result_schema(checklist))
            reports = {}
            for name, check in checklist.checks.items():
                report = checklist_report(results[name]) if check.rules else results[name]
                if report.get("context_requests"):
                    raise ValueError("Resolve requested evidence before recording a review")
                reports[name] = report
        except (ValueError, jsonschema.ValidationError) as error:
            raise CommandError(str(error)) from error
        report = {"valid": all(r["valid"] for r in reports.values()), "checks": reports,
                  "feedback": [f"[{name}] {f}" for name, r in reports.items() for f in r["feedback"]]}
        return self._save_review(
            index, sample, "validated" if report["valid"] else "rejected", reviewer,
            summary, expected_revision, report=report, origin="mcp_checklist_review",
            checklist_hash=checklist_hash,
        )

    @command(write=True)
    def sign_off_trajectory(
        self,
        sample_id: str,
        verdict: Literal["validated", "rejected"],
        reviewer: Annotated[str, Field(min_length=1, max_length=200)],
        summary: Annotated[str, Field(min_length=1, max_length=10000)],
        expected_revision: Nonnegative,
    ):
        """Record YOUR completed review without calling any model API.

        Read get_authoring_guide(topic="review") before reviewing or repairing.
        Zero compiler warnings do not establish semantic correctness. Check shared
        open-window context, redundant tools and dependencies throughout streams.
        After inspecting/repairing the trajectory, reread its current source and
        review it, then pass that revision. validated sets state 2; rejected sets
        state -2. Records the reviewer label (self-reported, e.g. GPT-6.1 via Codex),
        summary, timestamp and reviewed source hash. Successful sign-off requires
        compilation; compiler warnings remain your responsibility and are returned.
        This records your assessment, not an independent LLM review.
        Never grants Ready (3). Later content edits invalidate the sign-off.
        """
        index, sample = self._draft(sample_id, expected_revision)
        return self._save_review(index, sample, verdict, reviewer, summary, expected_revision)

    def _save_review(
        self, index, sample, verdict, reviewer, summary, expected_revision,
        *, report=None, origin="mcp_sign_off", checklist_hash=None,
    ):
        reviewer, summary = reviewer.strip(), summary.strip()
        if not reviewer or not summary:
            raise CommandError("Reviewer and summary must contain non-whitespace text")
        try:
            diagnostics = self.compiler.compile(sample)["diagnostics"]
        except CompileError as error:
            if verdict == "validated":
                raise
            diagnostics = [error.diagnostic()]
        metadata = sample.get("metadata")
        if not isinstance(metadata, dict):
            metadata = sample["metadata"] = {}
        source_hash = hashlib.sha256(
            json.dumps(evaluation_source(sample), sort_keys=True).encode()
        ).hexdigest()
        evaluation = {
            "level": 2,
            "origin": origin,
            "reviewer": reviewer,
            "summary": summary,
            "reviewed_revision": expected_revision,
            "report": report if report is not None else {"valid": verdict == "validated", "feedback": [summary]},
            "source_hash": source_hash,
            "tokenizer": self.compiler.fingerprint,
            "tools": self.compiler.tools.fingerprint,
            "time": datetime.now(UTC).isoformat(),
        }
        if checklist_hash is not None:
            evaluation["checklist_hash"] = checklist_hash
        evaluations = metadata.get("evaluations")
        if not isinstance(evaluations, list):
            evaluations = metadata["evaluations"] = []
        evaluations.append(evaluation)
        metadata["validation_level"] = 2 if verdict == "validated" else -2
        metadata.pop("approval", None)
        return {
            **self._commit(sample, index, review=True),
            "validation_level": metadata["validation_level"],
            "evaluation": evaluation,
            "diagnostics": diagnostics,
        }

    @command()
    async def validate_trajectory(self, sample_id: str, semantic: bool = False):
        """Compile a sample and report errors/warnings. semantic=True also calls configured LLM validators.

        No provider calls occur by default. Compiler errors return valid=false with a
        diagnostic path; the compiler stops at its first error. Warnings do not fail validation.
        This does not sign off a review; use sign_off_trajectory after your own review.
        """
        revision = self.state["revision"]
        guidance = {"tool": "get_guidance", "sample_id": sample_id,
                    "instruction": "Read the complete guidance bundle before semantic validation or repair."}
        try:
            plan = self._plan(sample_id)
        except CompileError as error:
            return {
                "valid": False,
                "diagnostics": [error.diagnostic()],
                "guidance": guidance,
                "revision": revision,
            }
        result = {
            "valid": True,
            "diagnostics": plan["diagnostics"],
            "max_tick": plan["max_tick"],
            "row_count": len(plan["rows"]),
            "guidance": guidance,
            "revision": revision,
        }
        if semantic:
            self._editable(None, sample_id)
            sample = copy.deepcopy(self._find(sample_id)[1])
            builder = SampleBuilder(
                self.compiler, copy.deepcopy(self.state["config"]), self.client
            )
            scenario = getattr(self.runner, "scenario_runner", None)
            if scenario and scenario.status["running"]:
                async with scenario.limit:
                    result["semantic"] = await builder.validators(sample)
            else:
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
        preview = self._preview(sample_id)
        plan = preview.plan
        end_tick = self._range(plan, start_tick, end_tick)
        life = next((w for w in plan["windows"] if w["id"] == window), None)
        if life is None:
            raise CommandError("Window not found", "not_found")
        matching = (
            r
            for r in plan["rows"]
            if r["window"] == window
            and start_tick <= r["tick"] <= end_tick
            and (include_noops or r["op"] != "noop")
        )
        rows, total = [], 0
        for row in matching:
            if offset <= total < offset + limit:
                rows.append(row)
            total += 1
        return {
            "revision": self.state["revision"],
            "window": life,
            "start_tick": start_tick,
            "end_tick": end_tick,
            "text": preview.frame(end_tick, [window])["windows"][window]["text"],
            "events": [
                e
                for e in plan["events"]
                if e["window"] == window
                and e["start"] <= end_tick
                and e["end"] >= start_tick
            ],
            "rows": rows,
            "total_rows": total,
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

        At most 100 frames and 4 MB of output per request, regardless of plan row count.
        Filter windows or narrow the range for larger trajectories. kv_tokens is zero
        for closed/pending windows; cached_tokens counts historical durable tokens.
        """
        preview = self._preview(sample_id)
        plan = preview.plan
        self._range(plan, start_tick, end_tick)
        ticks = range(start_tick, end_tick + 1, step)
        if len(ticks) > 100:
            raise CommandError(
                "Preview exceeds 100 frames; narrow the range or increase step"
            )
        known = {w["id"] for w in plan["windows"]}
        if windows is not None and not set(windows) <= known:
            raise CommandError("Unknown preview window", "not_found")
        frames, size = [], 0
        for tick in ticks:
            frame = preview.frame(tick, windows)
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
            "samples": before.copy()
            if isinstance(before, SQLiteSamples)
            else copy.deepcopy(before),
            "config": self.state["config"],
            "revision": self.state["revision"],
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
                    unchanged = (
                        json_equal(sample, originals.get(ident))
                        if isinstance(ident, str)
                        else any(json_equal(sample, old) for old in before)
                    )
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

    def _check_history_lock(self, direction, steps):
        scenario = getattr(self.runner, "scenario_runner", None)
        if scenario is None or not scenario.status["running"]:
            return
        source = self.history.undo_stack if direction == "undo" else self.history.redo_stack
        preview = self.state["samples"].copy()
        for entry in reversed(source[-steps:]):
            for operation in entry["inverse" if direction == "undo" else "forward"]:
                index = int(operation["path"].lstrip("/"))
                if operation["op"] != "add" and scenario.locked(preview[index]["id"]):
                    raise CommandError("History action changes the active trajectory", "busy")
                if operation.get("value", {}).get("id") == scenario.status["draft_id"]:
                    raise CommandError("History action changes the active trajectory", "busy")
                if operation["op"] == "add": preview.insert(index, operation["value"])
                elif operation["op"] == "remove": del preview[index]
                else: preview[index] = operation["value"]

    @command(write=True, destructive=True)
    def undo_edits(
        self,
        steps: Annotated[int, Field(ge=1, le=50)] = 1,
        expected_revision: Revision = None,
    ):
        """Undo source edits as one revision change. Stop generation first; the undo commits immediately."""
        self._editable(expected_revision)
        self._check_history_lock("undo", steps)
        return self.history.restore("undo", steps)

    @command(write=True, destructive=True)
    def redo_edits(
        self,
        steps: Annotated[int, Field(ge=1, le=50)] = 1,
        expected_revision: Revision = None,
    ):
        """Reapply undone source edits. A new source edit discards the redo branch."""
        self._editable(expected_revision)
        self._check_history_lock("redo", steps)
        return self.history.restore("redo", steps)

    @command()
    def list_simulated_tools(self, session_id: str | None = None):
        """Discover registered tool schemas, including invisible arguments available to authors.

        Dispatch visible calls with call_simulated_tool. Invisible arguments require
        invisible trajectory events. Built-ins are local simulations; custom modules
        follow the same tick protocol.
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
        auto_anchor: bool = False,
        tokens_per_tick: Annotated[int, Field(ge=1)] = 1,
        invisible: bool = False,
        source: str | None = None,
    ):
        """Schedule an executable synthetic tool event and compile before committing one undoable edit.

        Tool windows exist from tick 0; request and masked result share that window.
        Requests generate one token per tick and dispatch on the next tick after
        the last token. Delivery controls the masked result, with tokens_per_tick
        controlling streamed results. auto_anchor assigns an unused result-anchor
        ID marking the final result token. Optional content
        or segments replace the canonical <tool> request and support anchors/NOOPs.
        An EOS in request content abandons composition (arguments need not be
        complete). Generated EOS on a running tool window cancels its active calls
        and stops later observations. result-anchor then captures the EOS tick.
        Each tool runs every tick until is_done. Built-ins retain duration_ticks as
        a compatibility delay. outcome is legacy author data passed to the tool,
        rather than an output override. Save with save_dataset.
        invisible=True schedules external sandbox activity with no model tokens/window,
        call-budget use or implicit clock advance. source optionally identifies its actor.
        Hidden failures are compile diagnostics. Every registered tool may run invisibly.
        """
        index, sample = self._draft(sample_id, expected_revision)
        event = {
            "op": "invisible-tool-call" if invisible else "tool-call",
            "tool": tool,
            "arguments": arguments,
            "duration-ticks": duration_ticks,
        }
        if invisible:
            if (
                any(v is not None for v in (window, content, segments, loss, outcome))
                or delivery != "instant"
                or auto_anchor or tokens_per_tick != 1
            ):
                raise CommandError(
                    "Invisible calls cannot have windows, model content, outcomes or streamed delivery"
                )
            if source is not None:
                event["source"] = source
        else:
            if source is not None:
                raise CommandError("source is only supported for invisible calls")
            event.update(
                window=(
                    window
                    if window is not None
                    else f"tool-result-{uuid.uuid4().hex[:12]}"
                ),
                delivery=delivery,
            )
        if not invisible:
            event["tokens-per-tick"] = tokens_per_tick
        for key, value in (
            ("content", content),
            ("segments", segments),
            ("loss", loss),
        ):
            if value is not None:
                event[key] = value
        if outcome is not None:
            event["outcome"] = outcome
        address = self._insert(sample, tick, event, position)
        event = sample["replay"][tick][address["event_index"]]
        if auto_anchor:
            ids = [int(match[1]) for match in ANCHOR.finditer(json.dumps(sample))]
            ids.extend(
                e["result-anchor"]
                for group in sample["replay"].values()
                for e in group
                if type(e.get("result-anchor")) is int
            )
            event["result-anchor"] = max(ids, default=0) + 1
        plan = self.compiler.compile(sample)
        path = f"replay[{address['tick']}][{address['event_index']}]"
        call = next(c for c in plan["tool_calls"] if c["path"] == path)
        return {**self._commit(sample, index), **address, "tool_call": call}

    @command()
    def get_sandbox_state(
        self, sample_id: str, tick: Nonnegative, include_calls: bool = True
    ):
        """Inspect opaque historical sandbox state and separate tool statuses.

        Post-commit state at tick; running calls omit future results. Reading never
        executes external requests or mutates a workbench. Set include_calls=False
        to omit argument/outcome logs from a large preview. Maximum output: 4 MB.
        """
        plan = self._plan(sample_id)
        frame = self.compiler.frame(plan, tick)
        sandbox = frame["sandbox"]
        if len(json.dumps(sandbox, ensure_ascii=False).encode()) > 4_000_000:
            raise CommandError("Sandbox preview exceeds 4 MB; set include_calls=False")
        return {
            "sample_id": sample_id,
            "tick": tick,
            "sandbox": sandbox,
            **({"tool_calls": frame["tool_calls"]} if include_calls else {}),
            "revision": self.state["revision"],
        }

    @command(write=True)
    def create_tool_session(
        self,
        session_id: str | None = None,
        clock_seed: str | None = None,
        tick_rate: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None,
        max_calls: Annotated[int, Field(ge=1, le=256)] | None = None,
        max_bytes: Annotated[int, Field(ge=128, le=2_000_000)] = 2_000_000,
    ):
        """Create an isolated simulated-tool workbench (up to eight sessions).

        Defaults use the configured synthetic clock and call budget. Sessions are RAM-only,
        separate from generation sandboxes, and excluded from saves/source edit history.
        Subagent replies are supplied by the author, never requested from a provider.
        Subagents retain history; resume uses the returned conversationId. Only explicitly
        referenced artifact versions are shared; other parent RAM/trajectory context is excluded.
        """
        clock = self.state["config"].get("clock", {})
        options = {
            "seed": (
                clock_seed
                if clock_seed is not None
                else clock.get("seed", "2030-01-01T09:00:00+00:00")
            ),
            "tick_rate": tick_rate if tick_rate is not None else clock.get("tick_rate", 10),
            "max_calls": (
                max_calls
                if max_calls is not None
                else self.state["config"].get("max_tool_calls", 64)
            ),
            "max_bytes": max_bytes,
        }
        return self.tool_sessions.create(session_id, options)

    @command()
    def list_tool_sessions(self):
        """List live tool workbenches with options, quotas, and state counts."""
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

    @command(write=True, destructive=True)
    async def call_simulated_tool(
        self,
        session_id: str,
        name: str,
        arguments: dict,
        record_sample_id: str | None = None,
        expected_revision: Revision = None,
        outcome: dict | None = None,
    ):
        """Execute only in an ephemeral workbench; never changes a trajectory sandbox.

        Use create_tool_call(sample_id, tick, ...) for executable trajectory events.
        Discover exact name/arguments with list_simulated_tools. Valid dispatched failures
        return outcome.ok=false and consume clock/budget. Invalid arguments do not execute.
        record_sample_id also appends the actual call/result in one tick-0 tool window
        as one undoable source edit. Recording is text only; it does not copy workbench
        state or schedule executable replay events.
        Recording requires a compilable sample (or empty draft) and stopped generation.
        A rejected recording leaves virtual state/log unchanged. For subagent, supply
        outcome={ok:true,result:{response:"authored reply",conversationId:"optional-id"}}
        or an authored failure. This is author-only data, separate from model arguments.
        Built-ins are local simulations. Custom modules run with server permissions.
        """
        if expected_revision is not None and record_sample_id is None:
            raise CommandError(
                "expected_revision applies to recording; supply record_sample_id"
            )
        recorder = None
        context = None
        if record_sample_id is not None:
            self._editable(expected_revision, record_sample_id)
            _, sample = self._find(record_sample_id)
            windows = []
            if any(sample.get("replay", {}).values()):
                plan = self.compiler.compile(sample)
                frame = self.compiler.frame(plan, plan["max_tick"])
                windows = [WindowProperties(w["id"], w["type"], frame["windows"][w["id"]]["text"].removeprefix(w["header"]))
                           for w in plan["windows"] if w["close"] is None]
            metadata = sample.get("metadata", {})
            context = {"windows": windows, "agent_name": metadata.get("agent_name", "Kidra") if isinstance(metadata, dict) else "Kidra"}
            recorder = lambda entry: self._record_interaction(
                record_sample_id, entry, expected_revision
            )
        return await self.tool_sessions.call(
            session_id, name, arguments, recorder, context, outcome
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
            )
            .replace("{N_TICK", "\\u007bN_TICK")
            .replace("{WINDOW", "\\u007bWINDOW"),
            close=False,
            request_id=request_id,
        )
        builder.add_stream(
            result_window,
            tool_message(
                "tool_result", entry["outcome"], self.compiler.special.values()
            )
            .replace("{N_TICK", "\\u007bN_TICK")
            .replace("{WINDOW", "\\u007bWINDOW"),
            op="append",
            source="tool",
            close=False,
            loss={"mask": "exclude"},
            request_id=request_id,
        )
        for update in entry.get("window_updates", []):
            update_tick = str(builder.next_tick)
            builder.add_stream(
                update["window"],
                tool_text(update["content"], self.compiler.special.values())
                    .replace("{N_TICK", "\\{N_TICK").replace("{WINDOW", "\\{WINDOW"),
                op="append", source="tool", loss={"mask": "exclude"}, request_id=request_id,
            )
            if update["replace"]:
                # Recorded window observations preserve replacements without rerunning the tool.
                builder.sample["replay"][update_tick][-1]["replace-content"] = True
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
