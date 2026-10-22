"""Typed trajectory commands shared by the local API and the MCP bridge.

Edits operate on copies and commit once. Invalid drafts can be repaired across
several commands; validate=True makes a single edit conditional on compilation.
"""

from __future__ import annotations

import copy
import inspect
import json
import uuid
from collections import OrderedDict
from typing import Annotated, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .compiler import KEY, CompileError
from .dataset import new_sample
from .llm import SampleBuilder

Nonnegative = Annotated[int, Field(ge=0)]
PageSize = Annotated[int, Field(ge=1, le=5000)]
Revision = Nonnegative | None


class CommandError(ValueError):
    def __init__(self, message, code="invalid_arguments"):
        super().__init__(message)
        self.code = code


class TextPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: Nonnegative
    end: Nonnegative
    text: str


def command(*, write=False, destructive=False):
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
            "openWorldHint": method.__name__ == "validate_trajectory",
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
    def __init__(self, state, compiler, runner, client=None):
        self.state, self.compiler, self.runner = state, compiler, runner
        self.client = client
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
        result = fn(
            **{
                field.alias: getattr(model, name)
                for name, field in type(model).model_fields.items()
            }
        )
        if inspect.isawaitable(result):
            result = await result
        return copy.deepcopy(result)

    def _find(self, sample_id):
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
        ident = sample.get("id")
        if not isinstance(ident, str) or not ident:
            raise CommandError("A nonempty sample id is required")
        if any(
            i != index and other.get("id") == ident
            for i, other in enumerate(self.state["samples"])
        ):
            raise CommandError(f"Duplicate sample ID: {ident}", "conflict")
        # Reject non-JSON values before changing the live session.
        json.dumps(sample, allow_nan=False)
        if validate:
            self.compiler.compile(sample)
        if index is None:
            self.state["samples"].append(sample)
        else:
            self.state["samples"][index] = sample
        self.state["revision"] += 1
        return {"sample_id": ident, "revision": self.state["revision"], "saved": False}

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
            "status": self.runner.status,
            "tokenizer_sha256": self.compiler.fingerprint,
            "special_tokens": self.compiler.special,
        }

    @command()
    def list_trajectories(
        self, query: str = "", offset: Nonnegative = 0, limit: PageSize = 50
    ):
        """List trajectories with metadata; filter by ID or metadata text and paginate."""
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
    def get_trajectory(self, sample_id: str):
        """Return the complete, untruncated source sample JSON, including invalid drafts."""
        return {"sample": self._find(sample_id)[1], "revision": self.state["revision"]}

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
        """Remove a trajectory from the session. Call save_dataset to persist the deletion."""
        index, _ = self._draft(sample_id, expected_revision)
        self.state["samples"].pop(index)
        self.state["revision"] += 1
        return {
            "sample_id": sample_id,
            "revision": self.state["revision"],
            "saved": False,
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
        replay cannot be changed here. Renaming id requires a unique nonempty value.
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
        """Insert an open, append or generate event at a numeric or anchor tick key.

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

        Free-form content and metadata are not rewritten.
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
                    else "closed"
                    if life["close"] is not None and life["close"] <= tick
                    else "open"
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

    @command(write=True)
    async def save_dataset(self):
        """Atomically save the current session to its configured dataset file (no arbitrary paths)."""
        revision = await self.runner.save()
        return {"saved": True, "revision": revision}
