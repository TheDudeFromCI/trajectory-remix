"""Explicit, opt-in graph tokenization/scheduling and frontier-array lowering.

No source, settings, metrics or files are changed. The same scheduler drives
bounded visual previews and export. Ordinary graph operations do not import it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import random
from typing import Callable

from tokenizers import Tokenizer

from . import ktremix as k
from .models import GraphSource, ProjectSettings, TokenizerProfile, WindowReference, digest
from .validation import diagnose

VERSION = "kidra.graph-compiler/1"
CONTRACT = "kidra.progressive-sleep/1"


class CompileError(ValueError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Token:
    id: int | None
    control: str | None = None
    header: bool = False
    reference: tuple[str, int] | None = None


@dataclass(frozen=True)
class Emission:
    event: str | None
    window: str
    tokens: tuple[Token, ...]
    model: bool
    weight: float
    replace: bool = False
    opening: bool = False


@dataclass(frozen=True)
class Frame:
    tick: int
    inputs: tuple[Emission, ...]
    outputs: tuple[Emission, ...]


@dataclass
class Instance:
    logical: str | None
    slot: int
    writable: bool
    opened: int
    active: int = k.FOREVER
    closed: int = k.FOREVER
    initially_available: bool = False
    rows: list[int] = field(default_factory=list)
    header: list[int] = field(default_factory=list)


class Runtime:
    """Frontier state shared by compiler lowering and the tiny inference demo."""
    def __init__(self, active_limit: int, controls: dict[str, int], seed=0):
        if type(active_limit) is not int or active_limit < 1:
            raise CompileError("active_limit must be positive")
        self.limit = active_limit
        self.controls = controls
        self.rng = random.Random(seed)
        self.a = {name: [] for name in k.BY_NAME}
        self.a["frame_offsets"] = [0]
        self.frontier = 0
        self.instances: list[Instance] = []
        self.live: dict[str, int] = {}
        self.available = self._new(None, True)
        self.peak = 0
        self.trace = []
        self.references = []

    def _new(self, logical, writable):
        used = {w.slot for w in self.instances if w.closed == k.FOREVER}
        free = sorted(set(range(self.limit + 1)) - used)
        if not free:
            raise CompileError("Active window capacity exceeded (Available is excluded)")
        index = len(self.instances)
        self.instances.append(Instance(logical, self.rng.choice(free), writable, self.frontier,
                                       initially_available=logical is None))
        self.insert(index, Token(self.controls["bos"], "bos", True),
                    False, 0.)
        return index

    def open(self, logical, model):
        if logical in self.live:
            return self.live[logical]
        active = sum(w.active != k.FOREVER and w.closed == k.FOREVER for w in self.instances)
        if active >= self.limit:
            raise CompileError("Active window capacity exceeded (Available is excluded)")
        if model:
            index = self.available
            self.instances[index].logical = logical
        else:
            index = self._new(logical, self.writable[logical])
        self.live[logical] = index
        self.peak = max(self.peak, active + 1)
        return index

    def _latest(self, index):
        for row in reversed(self.instances[index].rows):
            if self.a["visible_from"][row] <= self.frontier < self.a["visible_until"][row]:
                return row
        return k.NONE

    def insert(self, index, token, model, weight, *, commit=None, previous=None):
        a, w = self.a, self.instances[index]
        previous = self._latest(index) if previous is None else previous
        position = 0 if previous == k.NONE else a["position_ids"][previous] + 1
        durable = token.control not in ("noop", "eos")
        flags = (k.DURABLE if durable else 0) | (k.MODEL if model else 0)
        flags |= {"bos": k.BOS, "eos": k.EOS, "noop": k.NOOP}.get(token.control, 0)
        boundary = self.frontier + 1 if commit is None else commit
        row = len(a["token_ids"])
        for name, value in dict(token_ids=token.id if token.id is not None else 0, window_ids=index, position_ids=position,
                                query_frontiers=self.frontier,
                                visible_from=boundary if durable else 0,
                                visible_until=k.FOREVER if durable else 0,
                                previous_rows=previous, loss_weights=weight,
                                row_flags=flags).items():
            a[name].append(value)
        if token.reference is not None:
            self.references.append((row, token.reference))
        if weight > 0:
            a["target_rows"].append(row)
        if durable:
            w.rows.append(row)
            if token.header:
                w.header.append(row)
        if commit is None:
            self.frontier = boundary
        return row

    def clear_body(self, index):
        # Old body expires exactly when the first replacement token commits.
        # Its direct own-window prefix is explicitly the retained header.
        w = self.instances[index]
        for row in w.rows:
            if row not in w.header:
                self.a["visible_until"][row] = min(self.frontier + 1, self.a["visible_until"][row])

    def close(self, index, boundary):
        w = self.instances[index]
        w.closed = boundary
        for row in w.rows:
            self.a["visible_until"][row] = min(boundary, self.a["visible_until"][row])
        if w.logical is not None:
            self.live.pop(w.logical, None)

    def apply_inputs(self, emissions):
        for emission in emissions:
            index = self.open(emission.window, False)
            if emission.replace:
                self.clear_body(index)
            for offset, token in enumerate(emission.tokens):
                previous = self.instances[index].header[-1] if emission.replace and offset == 0 else None
                self.insert(index, token, False, emission.weight, previous=previous)
                if token.control == "eos":
                    self.close(index, self.frontier)
            w = self.instances[index]
            if w.active == k.FOREVER:
                w.active = self.frontier

    def apply(self, frame: Frame):
        first = self.a["frame_offsets"][-1]
        self.apply_inputs(frame.inputs)
        # Bind at most one opening to the current Available, before shared prediction.
        for emission in frame.outputs:
            if emission.opening:
                self.open(emission.window, True)
        boundary = self.frontier + 1
        activate = None
        for emission in frame.outputs:
            index = self.available if emission.window == "@available" else self.live[emission.window]
            for token in emission.tokens:
                self.insert(index, token, True, emission.weight, commit=boundary)
                if token.control == "eos":
                    # Defer closure until all simultaneous predictions have used pre-EOS memory.
                    continue
                if token.control != "noop" and self.instances[index].active == k.FOREVER:
                    self.instances[index].active = boundary
                    if index == self.available:
                        activate = index
        for emission in frame.outputs:
            if any(t.control == "eos" for t in emission.tokens):
                self.close(self.live[emission.window], boundary)
        self.frontier = boundary
        if activate is not None:
            self.available = self._new(None, True)
        active = sum(w.active != k.FOREVER and w.closed == k.FOREVER for w in self.instances)
        self.peak = max(self.peak, active)
        # Opening+closing in one frame still counts toward peak capacity.
        self.trace.append((frame.tick, first, len(self.a["token_ids"])))
        self.a["frame_offsets"].append(len(self.a["token_ids"]))

    def resolve_references(self, hex_tokens):
        slots = {w.logical: w.slot for w in self.instances if w.logical is not None}
        for row, (logical, digit) in self.references:
            if logical not in slots:
                raise CompileError(f"Referenced window never opens: {logical}")
            self.a["token_ids"][row] = hex_tokens[f"{slots[logical]:08x}"[digit]]

    def columns(self):
        for name, values in dict(
            window_slots=[w.slot for w in self.instances],
            window_open_frontiers=[w.opened for w in self.instances],
            window_close_frontiers=[w.closed for w in self.instances],
            window_activation_frontiers=[w.active for w in self.instances],
            window_flags=[(0 if w.writable else k.READ_ONLY) |
                          (k.INITIALLY_AVAILABLE if w.initially_available else 0)
                          for w in self.instances],
        ).items():
            self.a[name] = values
        return self.a


class GraphCompiler:
    def __init__(self, profile: TokenizerProfile, *, active_limit=32, seed=0,
                 settings: ProjectSettings | None = None, max_rows=1_000_000):
        self.profile = TokenizerProfile.model_validate(profile.model_dump())
        self.controls = self.profile.special_tokens
        try:
            self.tokenizer = Tokenizer.from_str(json.dumps(self.profile.definition))
            # Added-token matching is disabled on a separate literal encoder. No
            # vocabulary mutation: native special IDs must also exist in the model.
            definition = self.profile.model_dump()["definition"]
            definition["added_tokens"] = []
            definition["post_processor"] = None
            self.literal = Tokenizer.from_str(json.dumps(definition))
        except Exception as error:
            raise CompileError(f"Invalid tokenizer profile: {error}") from error
        dedicated = {entry["id"]: entry["content"] for entry in self.profile.definition["added_tokens"]
                     if entry.get("special") is True}
        for name, token in self.controls.items():
            spelling = dedicated[token]
            if (self.tokenizer.id_to_token(token) != spelling or self.tokenizer.token_to_id(spelling) != token
                    or self.tokenizer.encode(spelling, add_special_tokens=False).ids != [token]):
                raise CompileError(f"Incompatible dedicated control: {name}")
        self.active_limit, self.seed, self.settings = active_limit, seed, settings
        if type(max_rows) is not int or max_rows < 1:
            raise ValueError("max_rows must be positive")
        if any(t >= 2**32 for t in self.controls.values()):
            raise CompileError("Control IDs must fit u32")
        self.max_rows = max_rows

    def text(self, value):
        try:
            ids = self.literal.encode(value, add_special_tokens=False).ids
        except Exception as error:
            raise CompileError(f"Literal text cannot be encoded: {error}") from error
        if set(ids) & set(self.controls.values()):
            raise CompileError("Literal text encodes as a control; use a tokenizer with literal coverage")
        if value and not ids:
            raise CompileError("Tokenizer discards nonempty literal content")
        return [Token(i) for i in ids]

    def control(self, name, *, header=False):
        return Token(self.controls[name], name, header)

    def header(self, source, window, slot):
        w = source.windows[window]
        # Versioned grammar: JSON fields use ordinary tokens, bounded by CTX.
        payload = json.dumps({"slot": slot, "purpose": w.purpose,
                              "reasoning_effort": w.reasoning_effort},
                             separators=(",", ":"), ensure_ascii=False)
        return [self.control("ctx_begin", header=True),
                *[Token(t.id, header=True) for t in self.text(payload)],
                self.control("ctx_end", header=True)]

    def reference_alphabet(self):
        result = {}
        for char in "0123456789abcdef":
            tokens = self.text(char)
            if len(tokens) != 1:
                raise CompileError("Window references require one ordinary token per hex digit")
            result[char] = tokens[0].id
        return result

    def event_tokens(self, event, runtime):
        tokens = []
        if event.kind == "tool_call" and event.insert_tool_begin:
            tokens += [self.control("tool_begin"), *self.text(json.dumps(event.tool)), *self.text(":")]
        if event.kind == "tool_response" and event.insert_response_begin:
            tokens.append(self.control("response_begin"))
        if event.kind == "chat_message":
            tokens += [self.control("msg_begin"), *self.text(json.dumps(event.chat.speaker)), *self.text(":")]
        pieces = event.content if isinstance(event.content, list) else [event.content]
        for piece in pieces:
            if isinstance(piece, WindowReference):
                # Fixed-width u32 hex, one ordinary token per digit. Allocation
                # can be resolved after scheduling even for a future/closed
                # logical window, without a token-length/allocation cycle.
                self.reference_alphabet()
                tokens += [self.control("window_ref"), *[
                    Token(None, reference=(piece.window, digit)) for digit in range(8)]]
            else:
                tokens += self.text(piece if isinstance(piece, str) else piece.text)
        if event.kind == "tool_call" and event.insert_tool_end:
            tokens.append(self.control("tool_end"))
        if event.kind == "tool_response" and event.insert_response_end:
            tokens.append(self.control("response_end"))
        if event.kind == "chat_message":
            tokens.append(self.control("msg_end"))
        if event.close_window:
            tokens.append(self.control("eos"))
        return tokens

    def _runtime_for(self, source):
        runtime = Runtime(self.active_limit, self.controls, digest([self.seed, source.id]))
        runtime.writable = {name: w.agent_writable for name, w in source.windows.items()}
        return runtime

    def frames(self, source: GraphSource, *, cancelled: Callable[[], bool] = lambda: False):
        yield from self._frames(source, self._runtime_for(source), cancelled=cancelled)

    def _frames(self, source, runtime, *, cancelled):
        """Yield schedule frames while incrementally lowering exactly the same plan."""
        errors = [d for d in diagnose(source, self.settings) if d.severity == "error"]
        if errors:
            raise CompileError("; ".join(f"{d.code}: {d.message}" for d in errors))
        pending = set(source.events)
        running = {}
        done = {}  # event -> (completion tick, model origin)
        started = set()
        tick = 0
        last_was_noop = False

        def model(event):
            return event.kind in ("agent_text", "tool_call")

        def ready(ident):
            e = source.events[ident]
            if not all(dep in done for dep in e.after):
                return False
            return all(t < tick or (t == tick and not was_model and
                       (model(e) or e.delivery.chain_immediately))
                       for t, was_model in (done[dep] for dep in e.after))

        def begin(ident):
            e = source.events[ident]
            opening = e.window not in runtime.live
            if opening:
                index = runtime.open(e.window, model(e))
                header = self.header(source, e.window, runtime.instances[index].slot)
            else:
                header = []
            body = self.event_tokens(e, runtime)
            if getattr(e, "replace_body", False) and not any(t.control != "eos" for t in body):
                raise CompileError("Replacement must encode nonempty content")
            running[ident] = [header + body if model(e) else body, opening, header]
            pending.remove(ident)

        while pending or running:
            if cancelled():
                raise Cancelled("Compilation cancelled")
            inputs, outputs = [], []
            busy = set()
            # External chains are consumed in dependency order, irrespective of map order.
            while True:
                candidates = sorted(i for i in pending if not model(source.events[i]) and ready(i))
                progressed = False
                for ident in sorted(set(running) | set(candidates)):
                    e = source.events[ident]
                    if model(e) or e.window in busy:
                        continue
                    if ident in pending:
                        begin(ident)
                    tokens, opening, header = running[ident]
                    count = len(tokens) if e.delivery.mode == "instant" else min(1, len(tokens))
                    fragment = tuple(header + tokens[:count])
                    emission = Emission(ident, e.window, fragment, False,
                                        0. if e.supervision.masked else e.supervision.weight,
                                        getattr(e, "replace_body", False) and ident not in started, opening)
                    # Apply input immediately so later same-phase events resolve references/state.
                    # Frame lowering below must not insert these rows a second time.
                    runtime.apply_inputs([emission])
                    started.add(ident)
                    inputs.append(emission)
                    running[ident] = [tokens[count:], False, []]
                    progressed = True
                    if not tokens[count:]:
                        done[ident] = (tick, False)
                        del running[ident]
                    else:
                        busy.add(e.window)
                if not progressed:
                    break
            busy = set()  # Input streaming does not suppress writable-window NOOP decisions.
            # A first agent write consumes Available; other first writes wait a tick.
            opening_used = False
            for ident in sorted(i for i in pending if model(source.events[i]) and ready(i)):
                e = source.events[ident]
                if e.window not in runtime.live:
                    if opening_used:
                        continue
                    opening_used = True
                begin(ident)
            for ident in sorted(list(running)):
                e = source.events[ident]
                if not model(e):
                    continue
                tokens, opening, header = running[ident]
                if e.window in busy:
                    continue
                fragment = tuple(tokens[:1])
                # Empty non-opening events complete without inventing a text token.
                if fragment:
                    busy.add(e.window)
                    outputs.append(Emission(ident, e.window, fragment, True,
                                            0. if e.supervision.masked else e.supervision.weight,
                                            opening=opening))
                running[ident] = [tokens[1:], False, []]
                if len(tokens) <= 1:
                    done[ident] = (tick, True)
                    del running[ident]
            for window, index in sorted(runtime.live.items()):
                if runtime.instances[index].writable and window not in busy:
                    outputs.append(Emission(None, window, (self.control("noop"),), True,
                                            source.defaults.noop_loss_weight))
            # When Available is being activated it already has an output this pass.
            if not any(e.opening for e in outputs):
                outputs.append(Emission(None, "@available", (self.control("noop"),), True,
                                        source.defaults.noop_loss_weight))
            frame = Frame(tick, tuple(inputs), tuple(outputs))
            # Inputs have already advanced the runtime; preserve frame's full row span.
            runtime.apply(Frame(tick, (), tuple(outputs)))
            if len(runtime.a["token_ids"]) > self.max_rows:
                raise CompileError("Compilation row bound exceeded")
            last_was_noop = all(t.control == "noop" for e in outputs for t in e.tokens)
            yield frame
            tick += 1
        # Keep one final all-NOOP decision when writable windows remain open.
        if not last_was_noop:
            outputs = [Emission(None, w, (self.control("noop"),), True, source.defaults.noop_loss_weight)
                       for w, i in sorted(runtime.live.items()) if runtime.instances[i].writable]
            outputs.append(Emission(None, "@available", (self.control("noop"),), True,
                                    source.defaults.noop_loss_weight))
            frame = Frame(tick, (), tuple(outputs))
            if cancelled():
                raise Cancelled("Compilation cancelled")
            runtime.apply(frame)
            if len(runtime.a["token_ids"]) > self.max_rows:
                raise CompileError("Compilation row bound exceeded")
            yield frame

    def compile(self, source, *, cancelled=lambda: False):
        source = GraphSource.model_validate(source.to_dict() if isinstance(source, GraphSource) else source)
        runtime = self._runtime_for(source)
        frames = list(self._frames(source, runtime, cancelled=cancelled))
        if runtime.references:
            runtime.resolve_references(self.reference_alphabet())
        return runtime.columns(), {
            "compiler": VERSION, "execution_contract": CONTRACT,
            "source_hash": source.intrinsic_hash, "profile": self.profile.fingerprint,
            "seed": self.seed, "peak_active_windows": runtime.peak,
            "model_ticks": [f.tick for f in frames],
            "logical_windows": [w.logical for w in runtime.instances],
        }

    def preview(self, source, *, max_frames=1000, cancelled=lambda: False):
        if type(max_frames) is not int or max_frames < 1:
            raise ValueError("max_frames must be positive")
        source = GraphSource.model_validate(source.to_dict() if isinstance(source, GraphSource) else source)
        frames = []
        truncated = False
        for frame in self.frames(source, cancelled=cancelled):
            if len(frames) == max_frames:
                truncated = True
                break
            frames.append(frame)
        return {"cache_key": digest([VERSION, source.intrinsic_hash, self.profile.fingerprint,
                                     self.seed, self.active_limit]),
                "source_hash": source.intrinsic_hash, "frames": frames, "truncated": truncated}
