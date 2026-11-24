"""Standalone native compiler, diagnostics, corpus conversion and shard sampling."""

from __future__ import annotations

import json
from itertools import islice

from . import _native

Cancelled = _native.Cancelled
CONTRACT = _native.CONTRACT
VERSION = _native.VERSION

__all__ = [
    "CONTRACT",
    "VERSION",
    "Cancelled",
    "Compiler",
    "Dataset",
    "Sample",
    "dry_run",
    "dry_runs",
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _source(value):
    return value if isinstance(value, str) else _json(value)


def dry_run(source, *, settings=None):
    """Return structural diagnostics for a source mapping or JSON string."""
    return json.loads(
        _native.report_json(_source(source), None if settings is None else _json(settings))
    )


def _batches(values, size):
    if type(size) is not int or size < 1:
        raise ValueError("queue_size must be positive")
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def dry_runs(sources, *, settings=None, workers=1, queue_size=16):
    """Yield structural reports for a bounded iterable of JSON sources."""
    snapshot = None if settings is None else _json(settings)
    for batch in _batches(map(_source, sources), queue_size):
        yield from json.loads(_native.reports_json(batch, workers, snapshot))


class Compiler:
    """Compile graph JSON with a tokenizer profile containing its dedicated control IDs."""

    def __init__(self, profile, *, settings=None, **options):
        self._compiler = _native.Compiler(
            _source(profile), _json(options), None if settings is None else _json(settings)
        )

    def compile(self, source, *, cancelled=None):
        """Return columns, provenance and diagnostics. Compiler errors raise ValueError."""
        return json.loads(self._compiler.compile_json(_source(source), cancelled))

    def dry_run(self, source):
        """Return the compilation diagnostics and token metrics without publishing a shard."""
        return json.loads(self._compiler.report_json(_source(source)))

    def dry_runs(self, sources, *, workers=1, queue_size=16):
        """Yield tokenizer-backed reports in input order."""
        for batch in _batches(map(_source, sources), queue_size):
            yield from json.loads(self._compiler.reports_json(batch, workers))

    def preview(self, source, *, max_frames=1000, cancelled=None):
        """Return schedule frames and a flag indicating truncation."""
        return json.loads(self._compiler.preview_json(_source(source), max_frames, cancelled))

    def literal_ids(self, text):
        return self._compiler.literal_ids(text)

    def tokenizer_json(self):
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
        """Compile an iterable of source mappings or JSON strings into one atomic shard."""
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
        """Compile an iterable of JSON file paths, reading files on native workers."""
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
        """Convert raw UTF-8 documents or JSONL text records into training shards."""
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
        """Convert a streaming iterable of document mappings into training shards."""
        return self._compiler.convert_documents(
            map(_source, documents),
            directory,
            _json(options),
            _json(provenance),
            max_document_bytes,
            cancelled,
        )


_DTYPES = {1: "u1", 3: "<u4", 4: "<u8", 6: "<f4"}


class Sample:
    """One training record. NumPy arrays borrow the immutable native buffer."""

    def __init__(self, sample):
        self._sample = sample

    @property
    def uuid(self):
        return self._sample.id

    @property
    def rows(self):
        return self._sample.rows

    @property
    def frames(self):
        return self._sample.frames

    @property
    def windows(self):
        return self._sample.windows

    @property
    def targets(self):
        return self._sample.targets

    @property
    def metadata(self):
        return json.loads(self._sample.metadata_json())

    def numpy(self):
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
    """Batched sampling from compatible KTREMIX shards."""

    def __init__(self, paths, **options):
        self._dataset = _native.Dataset(list(paths), _json(options))

    def __len__(self):
        return len(self._dataset)

    def reset_epoch(self, epoch):
        self._dataset.reset_epoch(epoch)

    def next_batch(self):
        return [Sample(sample) for sample in self._dataset.next_batch()]
