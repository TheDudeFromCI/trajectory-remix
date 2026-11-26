"""Compile Trajectory Remix graphs and raw text into KTREMIX training shards.

This is a thin Python layer over the native Rust ``ktremix`` crate. It needs no
Trajectory Remix installation. The main entry points are:

* :class:`Compiler`: compile graphs, run diagnostics, write shards, convert corpora.
* :func:`dry_run` / :func:`dry_runs`: tokenizer-free structural checks.
* :class:`Dataset`: batched sampling from written shards.

Graph sources can be passed as mappings or JSON strings. Results are plain
dictionaries and lists. Long-running calls accept a ``cancelled`` callable that
returns true to stop the work; it raises :class:`Cancelled`.
"""

from __future__ import annotations

import json
from itertools import islice

from . import _native

# Raised when a ``cancelled`` callback asks native work to stop.
Cancelled = _native.Cancelled
# Execution contract and compiler version recorded in every shard manifest.
CONTRACT = _native.CONTRACT
VERSION = _native.VERSION
DEFAULT_MAX_ROWS = _native.DEFAULT_MAX_ROWS

__all__ = [
    "CONTRACT",
    "DEFAULT_MAX_ROWS",
    "VERSION",
    "Cancelled",
    "Compiler",
    "Dataset",
    "Sample",
    "dry_run",
    "dry_runs",
]


def _json(value):
    """Serialize compactly and strictly (no NaN), the form the native layer expects."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _source(value):
    """Accept a graph as a mapping or as a JSON string already."""
    return value if isinstance(value, str) else _json(value)


def dry_run(source, *, settings=None):
    """Check a graph's structure without a tokenizer.

    Validates shape, window lifecycle, references and (given ``settings``, a
    project settings snapshot) tool contracts. Returns ``{"diagnostics": [...]}``;
    warnings do not block compilation, errors do. Token metrics are not available
    without a tokenizer, so use :meth:`Compiler.dry_run` for those.
    """
    return json.loads(
        _native.report_json(_source(source), None if settings is None else _json(settings))
    )


def _batches(values, size):
    """Yield lists of at most ``size`` items, so only one batch is held in memory."""
    if type(size) is not int or size < 1:
        raise ValueError("queue_size must be positive")
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def dry_runs(sources, *, settings=None, workers=1, queue_size=16):
    """Yield :func:`dry_run` reports in input order, ``queue_size`` graphs at a time.

    ``workers`` native threads check each batch in parallel. One ``settings``
    snapshot is shared by every graph.
    """
    snapshot = None if settings is None else _json(settings)
    for batch in _batches(map(_source, sources), queue_size):
        yield from json.loads(_native.reports_json(batch, workers, snapshot))


class Compiler:
    """Compiles graphs for one tokenizer profile.

    ``profile`` is a mapping or JSON string with ``name``, the Hugging Face
    tokenizer JSON in ``definition``, and ``special_tokens`` naming the control
    token IDs. ``settings`` is an optional project settings snapshot used to
    check tool contracts and reasoning levels. Remaining keyword options are
    ``active_limit`` (most simultaneously active windows), ``seed`` (slot
    assignment) and ``max_rows``. Build one ``Compiler`` and reuse it.
    """

    def __init__(self, profile, *, settings=None, **options):
        self._compiler = _native.Compiler(
            _source(profile), _json(options), None if settings is None else _json(settings)
        )

    def compile(self, source, *, cancelled=None):
        """Compile one graph.

        Returns a dictionary with ``columns`` (token, window and loss arrays),
        ``id``, ``source_hash``, ``logical_windows`` and ``report``. A graph with
        diagnostics of severity ``error`` raises ``ValueError``.
        """
        return json.loads(self._compiler.compile_json(_source(source), cancelled))

    def dry_run(self, source):
        """Run the full scheduler without keeping its output.

        Returns ``diagnostics`` plus ``metrics`` (total rows, target rows, peak active
        windows and peak visible rows) when scheduling succeeds. Errors are reported,
        not raised.
        """
        return json.loads(self._compiler.report_json(_source(source)))

    def dry_runs(self, sources, *, workers=1, queue_size=16):
        """Yield :meth:`dry_run` reports in input order, ``queue_size`` graphs at a time."""
        for batch in _batches(map(_source, sources), queue_size):
            yield from json.loads(self._compiler.reports_json(batch, workers))

    def preview(self, source, *, max_frames=1000, cancelled=None):
        """Return ``(frames, truncated)`` for display.

        Each frame lists the external inputs and model outputs scheduled for one step,
        up to ``max_frames`` frames. No arrays are built.
        """
        return json.loads(self._compiler.preview_json(_source(source), max_frames, cancelled))

    def literal_ids(self, text):
        """Tokenize ``text`` as plain text, with no control tokens added."""
        return self._compiler.literal_ids(text)

    def tokenizer_json(self):
        """Return the profile's tokenizer as Hugging Face JSON."""
        return self._compiler.tokenizer_json()

    def write_trajectories(
        self,
        path,
        sources,
        *,
        sample_count,
        manifest=None,
        codec="lz4",
        workers=1,
        queue_size=16,
        cancelled=None,
    ):
        """Compile graphs into one shard at ``path`` and return ``path``.

        ``sample_count`` must equal the number of graphs supplied. ``manifest`` adds
        entries to the shard manifest. ``codec`` is ``"lz4"``, ``"zlib"`` or ``"none"``.
        The shard only appears once every graph has compiled; on any error nothing
        is left behind. ``queue_size`` bounds how many graphs are in flight.
        """
        return self._write(
            path,
            map(_source, sources),
            "append",
            sample_count,
            manifest,
            codec,
            workers,
            queue_size,
            cancelled,
        )

    def write_files(
        self,
        path,
        sources,
        *,
        sample_count,
        manifest=None,
        codec="lz4",
        workers=1,
        queue_size=16,
        cancelled=None,
    ):
        """Like :meth:`write_trajectories`, but ``sources`` are JSON file paths.

        Files are read on the native workers, so the Python process never holds the
        source text. Results are written in input order.
        """
        return self._write(
            path,
            sources,
            "append_files",
            sample_count,
            manifest,
            codec,
            workers,
            queue_size,
            cancelled,
        )

    def _write(self, path, sources, method, count, manifest, codec, workers, queue_size, cancelled):
        if type(count) is not int or count < 0:
            raise ValueError("sample_count must be nonnegative")
        combined = json.loads(self._compiler.manifest_json()) | (manifest or {})
        writer = _native.ShardWriter(path, _json(combined), count, codec, workers)
        append = getattr(writer, method)
        # The native layer only polls ``cancelled`` while scheduling, so also check
        # between batches.
        for batch in _batches(sources, queue_size):
            if cancelled is not None and cancelled():
                raise Cancelled("Compilation cancelled")
            append(self._compiler, batch, cancelled)
        if cancelled is not None and cancelled():
            raise Cancelled("Compilation cancelled")
        writer.finish()
        return path

    def convert_corpus(
        self,
        paths,
        directory,
        *,
        input="jsonl",
        provenance=None,
        max_document_bytes=16 << 20,
        cancelled=None,
        **options,
    ):
        """Convert raw text files into training shards under ``directory``.

        ``input`` is ``"jsonl"`` (one ``{"id", "text", "provenance"}`` object per line)
        or ``"text"`` (one document per file). ``provenance`` is recorded in the shard
        metadata; use a distinct ``revision`` for each distinct corpus. Other keyword
        options (``seed``, ``documents_per_sample``, ``retained_sections``,
        ``samples_per_shard``, ``workers``, ``resume``, ...) tune the conversion; see
        the package README. Returns the shard paths.
        """
        return self._compiler.convert_corpus(
            list(paths),
            directory,
            input,
            _json(options),
            _json(provenance),
            max_document_bytes,
            cancelled,
        )

    def convert_documents(
        self,
        documents,
        directory,
        *,
        provenance=None,
        max_document_bytes=16 << 20,
        cancelled=None,
        **options,
    ):
        """Like :meth:`convert_corpus`, but ``documents`` is an iterable of mappings.

        Each mapping needs ``text`` and may have ``id`` and ``provenance``. Useful
        for corpora already loaded by another library, such as PyArrow batches.
        """
        return self._compiler.convert_documents(
            map(_source, documents),
            directory,
            _json(options),
            _json(provenance),
            max_document_bytes,
            cancelled,
        )


# Column dtype codes used by the native shard format, mapped to NumPy dtype strings.
_DTYPES = {1: "u1", 3: "<u4", 4: "<u8", 6: "<f4"}


class Sample:
    """One training record. NumPy arrays borrow the immutable native buffer."""

    def __init__(self, sample):
        self._sample = sample

    @property
    def uuid(self):
        """The sample's UUID."""
        return self._sample.id

    @property
    def rows(self):
        """Number of rows (token occurrences)."""
        return self._sample.rows

    @property
    def frames(self):
        """Number of frames."""
        return self._sample.frames

    @property
    def windows(self):
        """Number of window instances."""
        return self._sample.windows

    @property
    def targets(self):
        """Number of rows with positive loss weight."""
        return self._sample.targets

    @property
    def metadata(self):
        """Sample metadata (source provenance and similar) as a dictionary."""
        return json.loads(self._sample.metadata_json())

    def numpy(self):
        """Return every column as a read-only NumPy array keyed by column name.

        The arrays are views of the native buffer (no copy) and keep it alive.
        """
        import numpy as np

        buffer = memoryview(self._sample)
        return {
            name: np.frombuffer(
                buffer,
                dtype=_DTYPES[dtype],
                count=size // np.dtype(_DTYPES[dtype]).itemsize,
                offset=offset,
            )
            for name, dtype, offset, size in self._sample.columns()
        }


class Dataset:
    """Batched sampling from compatible KTREMIX shards.

    Keyword options: ``batch_size``, ``shard_weights``, ``without_replacement``,
    ``seed``, ``max_open_shards`` and ``decode_workers``. See the package README.
    """

    def __init__(self, paths, **options):
        self._dataset = _native.Dataset(list(paths), _json(options))

    def __len__(self):
        return len(self._dataset)

    def reset_epoch(self, epoch):
        """Begin a reproducible new epoch and clear any earlier failure."""
        self._dataset.reset_epoch(epoch)

    def next_batch(self):
        """Return the next batch of :class:`Sample`.

        With ``without_replacement`` the last batch may be short, then empty.
        """
        return [Sample(sample) for sample in self._dataset.next_batch()]
