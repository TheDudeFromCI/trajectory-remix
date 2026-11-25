"""Compatibility adapter from studio DTOs to the standalone KTREMIX compiler.

The compiler itself lives in the native ``ktremix`` Rust crate (see
``native/ktremix``). This module keeps the studio's historical ``GraphCompiler``
API: it accepts :class:`GraphSource` and :class:`TokenizerProfile` models,
forwards the work to ``ktremix_native``, and turns native ``ValueError`` failures
into :class:`CompileError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ktremix_native import CONTRACT, DEFAULT_MAX_ROWS, VERSION, Cancelled, Compiler
from tokenizers import Tokenizer

from .models import GraphSource, ProjectSettings, TokenizerProfile, digest

__all__ = ["CONTRACT", "VERSION", "Cancelled", "CompileError", "GraphCompiler"]


class CompileError(ValueError):
    """The graph, profile or options could not be compiled."""


@dataclass(frozen=True)
class Token:
    """One token of a preview frame: a literal ID, a control, or a window reference.

    ``header`` marks tokens of the window header. ``reference`` is the referenced
    window name and slot digit; its ``id`` is ``None`` until slots are assigned.
    """

    id: int | None
    control: str | None = None
    header: bool = False
    reference: tuple[str, int] | None = None


@dataclass(frozen=True)
class Emission:
    """Tokens one event writes into one window during a frame.

    ``model`` is true for tokens the model must predict and false for inputs.
    ``replace`` rewrites the window body; ``opening`` marks the window's first write.
    """

    event: str | None
    window: str
    tokens: tuple[Token, ...]
    model: bool
    weight: float
    replace: bool = False
    opening: bool = False


@dataclass(frozen=True)
class Frame:
    """One scheduler step: external ``inputs`` arrive, then the model emits ``outputs``."""

    tick: int
    inputs: tuple[Emission, ...]
    outputs: tuple[Emission, ...]


def _frames(values):
    """Rebuild typed frames from the plain dictionaries the native preview returns."""

    def emission(value):
        tokens = tuple(
            Token(
                **(token | {"reference": tuple(token["reference"]) if token["reference"] else None})
            )
            for token in value["tokens"]
        )
        return Emission(**(value | {"tokens": tokens}))

    return [
        Frame(f["tick"], tuple(map(emission, f["inputs"])), tuple(map(emission, f["outputs"])))
        for f in values
    ]


def _source(source):
    """Normalize a :class:`GraphSource` or raw mapping into a validated ``GraphSource``."""
    return GraphSource.model_validate(
        source.to_dict() if isinstance(source, GraphSource) else source
    )


class GraphCompiler:
    """Compiles studio graphs for one tokenizer profile.

    ``active_limit`` caps simultaneously active windows, ``seed`` fixes physical
    slot assignment (same source and seed give identical arrays), ``max_rows``
    bounds a single trajectory, and ``settings`` supplies project tool and
    reasoning-level contracts for validation. Raises :class:`CompileError` if the
    native compiler rejects the profile or options.
    """

    def __init__(
        self,
        profile: TokenizerProfile,
        *,
        active_limit=32,
        seed=0,
        settings: ProjectSettings | None = None,
        max_rows=DEFAULT_MAX_ROWS,
    ):
        self.profile = TokenizerProfile.model_validate(profile.model_dump())
        self.controls = self.profile.special_tokens
        self.active_limit, self.seed, self.settings = active_limit, seed, settings
        if type(max_rows) is not int or max_rows < 1:
            raise ValueError("max_rows must be positive")
        self.max_rows = max_rows
        try:
            self._native = Compiler(
                self.profile.model_dump(mode="json"),
                settings=None
                if settings is None
                else settings.model_dump(mode="json", include={"tools", "reasoning_levels"}),
                active_limit=active_limit,
                seed=seed,
                max_rows=max_rows,
            )
        except ValueError as error:
            raise CompileError(str(error)) from error
        self._tokenizer_json = self._native.tokenizer_json()

    @cached_property
    def tokenizer(self):
        """The profile's tokenizer as a ``tokenizers.Tokenizer``, built on first use."""
        return Tokenizer.from_str(self._tokenizer_json)

    def tokenizer_json(self):
        """The tokenizer as Hugging Face JSON text."""
        return self._tokenizer_json

    def text(self, value):
        """Tokenize ``value`` as literal text (no control tokens)."""
        try:
            return [Token(ident) for ident in self._native.literal_ids(value)]
        except ValueError as error:
            raise CompileError(str(error)) from error

    def frames(self, source, *, cancelled=None):
        """Yield every scheduled frame of ``source`` (up to ``max_rows``)."""
        yield from self.preview(source, max_frames=self.max_rows, cancelled=cancelled)["frames"]

    def compile(self, source, *, cancelled=None):
        """Compile one graph.

        Returns ``(columns, provenance)``: the row, frame and window arrays plus a
        dictionary describing how they were made (compiler version, source and
        profile hashes, seed, peak active windows, logical window names).
        """
        source = _source(source)
        try:
            result = self._native.compile(source.to_dict(), cancelled=cancelled)
        except ValueError as error:
            raise CompileError(str(error)) from error
        columns = result["columns"]
        return columns, {
            "compiler": VERSION,
            "execution_contract": CONTRACT,
            "source_hash": source.intrinsic_hash,
            "profile": self.profile.fingerprint,
            "seed": self.seed,
            "peak_active_windows": result["report"]["metrics"]["peak_active_windows"],
            "model_ticks": list(range(len(columns["frame_offsets"]) - 1)),
            "logical_windows": result["logical_windows"],
        }

    def preview(self, source, *, max_frames=1000, cancelled=None):
        """Schedule ``source`` and return up to ``max_frames`` frames for display.

        The result includes a ``cache_key`` that changes whenever the compiler
        version, source, profile, seed or window limit changes, and ``truncated``
        when more frames existed.
        """
        if type(max_frames) is not int or max_frames < 1:
            raise ValueError("max_frames must be positive")
        source = _source(source)
        try:
            frames, truncated = self._native.preview(
                source.to_dict(), max_frames=max_frames, cancelled=cancelled
            )
        except ValueError as error:
            raise CompileError(str(error)) from error
        return {
            "cache_key": digest(
                [
                    VERSION,
                    source.intrinsic_hash,
                    self.profile.fingerprint,
                    self.seed,
                    self.active_limit,
                ]
            ),
            "source_hash": source.intrinsic_hash,
            "frames": _frames(frames),
            "truncated": truncated,
        }

    def write_shard(self, path, sources, *, sample_count, manifest, cancelled=None, workers=1):
        """Compile ``sources`` into one shard at ``path`` and return the path.

        ``sample_count`` must match the number of sources; ``manifest`` is merged
        into the shard manifest. Nothing is published if any source fails.
        """
        try:
            return self._native.write_trajectories(
                path,
                sources,
                sample_count=sample_count,
                manifest=manifest,
                cancelled=cancelled,
                workers=workers,
            )
        except ValueError as error:
            raise CompileError(str(error)) from error
