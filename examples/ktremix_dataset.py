#!/usr/bin/env python3
"""KTREMIX 1.0 reference reader, sharded sampler and fixture writer.

Public companion to docs/KTREMIX.md. Python 3.11+; install lz4 for the default
codec and numpy for training-array views. No Remix, PyTorch or pickle dependency.
This does not implement the authoring compiler or a Kidra trainer.

  python -m pip install lz4 numpy
  python examples/ktremix_dataset.py demo /tmp/ktremix-demo
  python examples/ktremix_dataset.py sample '/tmp/ktremix-demo/*.ktremix' --count 5
  python examples/ktremix_dataset.py verify '/tmp/ktremix-demo/*.ktremix'
"""
from __future__ import annotations

import argparse
import array
import bisect
import glob
import hashlib
import json
import mmap
import os
import random
import struct
import sys
import tempfile
import uuid
import zlib
from collections import OrderedDict
from pathlib import Path

MAGIC = b"KTREMIX\0"
RECORD_MAGIC = b"KTSAMP\0\0"
HEADER = struct.Struct("<8sHHI6QIIQ16s16sIII4s")  # 128 bytes
INDEX = struct.Struct("<16sQ6IBBHIQ")             # 64 bytes
RECORD = struct.Struct("<8sHHI16s8I")             # 64 bytes
COLUMN = struct.Struct("<HBBQQQI")               # 32 bytes
assert (HEADER.size, INDEX.size, RECORD.size, COLUMN.size) == (128, 64, 64, 32)
ALIGNMENT = 64
NONE = (1 << 32) - 1
FOREVER = (1 << 64) - 1
DURABLE, MODEL, NOOP, EOS, BOS = 1, 2, 4, 8, 16
READ_ONLY, INITIALLY_AVAILABLE = 1, 2
CODECS = {"none": 0, "lz4": 1, "zlib": 2}
# dtype ID -> struct/array code, byte width, NumPy dtype
DTYPES = {1: ("B", 1, "u1"), 3: ("I", 4, "<u4"),
          4: ("Q", 8, "<u8"), 6: ("f", 4, "<f4")}
# column ID -> name, dtype ID, dimension
COLUMNS = {
    1: ("token_ids", 3, "R"),
    2: ("window_ids", 3, "R"),
    3: ("position_ids", 3, "R"),
    4: ("query_frontiers", 4, "R"),
    5: ("visible_from", 4, "R"),
    6: ("visible_until", 4, "R"),
    7: ("previous_rows", 3, "R"),
    8: ("loss_weights", 6, "R"),
    9: ("row_flags", 1, "R"),
    10: ("frame_offsets", 4, "F+1"),
    11: ("window_slots", 3, "W"),
    12: ("window_open_frontiers", 4, "W"),
    13: ("window_close_frontiers", 4, "W"),
    14: ("window_activation_frontiers", 4, "W"),
    15: ("window_flags", 1, "W"),
    16: ("target_rows", 3, "Q"),
}
BY_NAME = {value[0]: (key, *value[1:]) for key, value in COLUMNS.items()}


class FormatError(ValueError):
    """A file violates the wire format or requested training compatibility."""


def align(n):
    return (n + 63) & ~63


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def crc(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def _json(data, what):
    try:
        def invalid(value):
            raise ValueError(f"nonfinite JSON: {value}")

        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError(f"duplicate key: {key}")
                result[key] = value
            return result

        result = json.loads(
            bytes(data).decode("utf-8"),
            parse_constant=invalid,
            object_pairs_hook=pairs,
        )
    except (ValueError, UnicodeError) as error:
        raise FormatError(f"Invalid {what} JSON") from error
    if not isinstance(result, dict):
        raise FormatError(f"{what} must be a JSON object")
    return result


def _lz4():
    try:
        import lz4.block
    except ImportError as error:
        raise RuntimeError("LZ4 record: install 'lz4' (python -m pip install lz4)") from error
    return lz4.block


def _compatibility(manifest):
    try:
        if manifest["format"] != "ktremix" or manifest["version"] != "1.0":
            raise ValueError("manifest format/version")
        if manifest["array_schema"] != "ktremix.frontier-arrays/1":
            raise ValueError("array schema")
        execution = manifest["execution_contract"]
        if not isinstance(execution, str) or not execution:
            raise ValueError("execution contract")
        tokenizer = manifest["tokenizer"]
        digest = tokenizer["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or bytes.fromhex(digest).hex() != digest:
            raise ValueError("tokenizer SHA-256")
        controls = tokenizer["special_token_ids"]
        for name in ("bos", "eos", "noop"):
            value = controls[name]
            if type(value) is not int or not 0 <= value <= NONE:
                raise ValueError("control token ID")
        if len({controls[name] for name in ("bos", "eos", "noop")}) != 3:
            raise ValueError("control tokens must differ")
        for value in controls.values():
            if type(value) is not int or not 0 <= value <= NONE:
                raise ValueError("control token ID")
        # Fingerprint actual tokenizer identity, all control IDs and model contract.
        # A dataset may combine shards only when tokenization and training
        # semantics agree, even if their dataset IDs differ.
        return canonical(
            {
                "array_schema": manifest["array_schema"],
                "execution_contract": execution,
                "tokenizer": {
                    "sha256": digest,
                    "special_token_ids": controls,
                },
            }
        )
    except (KeyError, TypeError, ValueError) as error:
        raise FormatError(f"Invalid manifest: {error}") from error


class Sample:
    """One owned decompressed buffer (or mmap view) and zero-copy column views."""

    def __init__(self, payload, entry):
        self._data = memoryview(payload)
        self.uuid, _, _, raw_size, rows, frames, windows, _, _, _, _, targets, _ = entry
        self.uuid = str(uuid.UUID(bytes=self.uuid))
        self.rows = rows
        self.frames = frames
        self.windows = windows
        self.targets = targets
        if len(self._data) != raw_size or raw_size < RECORD.size:
            raise FormatError("Record size mismatch")
        fields = RECORD.unpack_from(self._data)
        magic, major, minor, size, ident, count, meta_size, r, f, w, q, entry_size, reserved = fields
        if (magic, major, minor, size, entry_size, reserved) != (RECORD_MAGIC, 1, 0, 64, 32, 0):
            raise FormatError("Unsupported record header")
        if ident != entry[0] or (r, f, w, q) != (rows, frames, windows, targets):
            raise FormatError("Index/record identity or counts disagree")
        directory_end = 64 + count * 32
        self._metadata_start, self._metadata_size = directory_end, meta_size
        first_array = align(directory_end + meta_size)
        if count < len(COLUMNS) or first_array > len(self._data):
            raise FormatError("Invalid column directory or metadata bounds")
        counts = {"R": rows, "F+1": frames + 1, "W": windows, "Q": targets}
        self._columns, seen, extents = {}, set(), []
        for i in range(count):
            directory_offset = 64 + i * 32
            cid, dtype, flags, offset, length, byte_size, reserved = COLUMN.unpack_from(
                self._data, directory_offset
            )
            if cid in seen or reserved or flags & ~1:
                raise FormatError("Duplicate column or unsupported directory flags")
            seen.add(cid)
            if offset < first_array or offset % 64 or offset + byte_size > len(self._data):
                raise FormatError("Column out of bounds or unaligned")
            if dtype not in DTYPES:
                if flags & 1:
                    raise FormatError("Unsupported required column dtype")
            elif byte_size != length * DTYPES[dtype][1]:
                raise FormatError("Column byte/count mismatch")
            if byte_size:
                extents.append((offset, offset + byte_size))
            if cid not in COLUMNS:
                if flags & 1:
                    raise FormatError("Unknown required column")
                continue
            name, expected_dtype, dimension = COLUMNS[cid]
            if dtype != expected_dtype or length != counts[dimension] or flags != 1:
                raise FormatError(f"Wrong dtype/count/flags for {name}")
            self._columns[name] = (dtype, offset, byte_size)
        if len(self._columns) != len(COLUMNS):
            raise FormatError("Missing required column")
        extents.sort()
        if any(a[1] > b[0] for a, b in zip(extents, extents[1:])):
            raise FormatError("Overlapping columns")

    @property
    def metadata(self):
        """Parse optional JSON only on explicit request; never on the training path."""
        if not self._metadata_size:
            return {}
        start = self._metadata_start
        return _json(self._data[start:start + self._metadata_size], "sample metadata")

    def column(self, name):
        """Native memoryview, little-endian hosts only. NumPy supports either host."""
        if sys.byteorder != "little":
            raise RuntimeError("Use numpy() for explicit little-endian views on this host")
        dtype, offset, size = self._columns[name]
        return self._data[offset:offset + size].cast(DTYPES[dtype][0])

    def numpy(self):
        """Read-only NumPy views; no per-token Python objects or extra data copies."""
        import numpy as np

        result = {}
        for name, (dtype, offset, size) in self._columns.items():
            value = np.frombuffer(
                self._data,
                dtype=DTYPES[dtype][2],
                count=size // DTYPES[dtype][1],
                offset=offset,
            )
            value.flags.writeable = False
            result[name] = value
        return result

    def validate(self):
        """Optional core semantic checks, O(rows + frames + windows*log(windows))."""
        import numpy as np
        a = self.numpy()
        fail = lambda condition, message: _require(bool(condition), message)
        fail(self.rows > 0 and self.frames > 0 and self.windows > 0, "Empty sample")
        offsets = a["frame_offsets"]
        fail(offsets[0] == 0 and offsets[-1] == self.rows and
             np.all(offsets[1:] > offsets[:-1]), "Invalid frame offsets")
        fail(np.all(a["window_ids"] < self.windows), "Invalid row window")
        weights = a["loss_weights"]
        fail(np.all(np.isfinite(weights)) and np.all(weights >= 0), "Invalid loss weights")
        fail(np.array_equal(a["target_rows"], np.flatnonzero(weights > 0)), "Incorrect target_rows")
        flags = a["row_flags"]
        fail(np.all((flags & 0xE0) == 0), "Unknown row flags")
        durable = (flags & DURABLE) != 0
        noop = (flags & NOOP) != 0
        fail(np.all(~(noop & durable)), "NOOP cannot be durable")
        fail(np.all(~noop | ((flags & MODEL) != 0)), "NOOP must be model-owned")
        control_count = sum(((flags & bit) != 0).astype("u1") for bit in (NOOP, EOS, BOS))
        fail(np.all(control_count <= 1), "Conflicting control flags")
        bos = (flags & BOS) != 0
        fail(np.all(durable[bos]) and np.all(weights[bos] == 0), "BOS is zero-loss durable context")
        start, end = a["visible_from"], a["visible_until"]
        fail(np.all(start[durable] <= end[durable]), "Invalid visibility interval")
        fail(np.all(start[~durable] == 0) and np.all(end[~durable] == 0),
             "Nondurable row has visibility")
        wid = a["window_ids"]
        opened, closed, active = (a[n] for n in ("window_open_frontiers", "window_close_frontiers",
                                                 "window_activation_frontiers"))
        fail(np.all(opened <= closed), "Invalid window lifetime")
        fail(np.all((active == FOREVER) | ((active >= opened) & (active <= closed))),
             "Invalid activation frontier")
        fail(np.all((a["window_flags"] & 0xFC) == 0), "Unknown window flags")
        read_only = (a["window_flags"][wid] & READ_ONLY) != 0
        fail(np.all(~(read_only & ((flags & MODEL) != 0))), "Model writes into read-only window")
        fail(np.all(start[durable] >= opened[wid[durable]]) and
             np.all(end[durable] <= closed[wid[durable]]), "Visibility exceeds window lifetime")
        fail(np.all(a["query_frontiers"][durable] < start[durable]),
             "Memory row leaks into its own construction")
        # Physical slots may be reused, but never by overlapping window instances.
        order = np.lexsort((opened, a["window_slots"]))
        previous = {}
        for index in order:
            slot = int(a["window_slots"][index])
            fail(int(opened[index]) >= previous.get(slot, 0), "Overlapping slot lifetimes")
            previous[slot] = int(closed[index])
        rows = np.arange(self.rows, dtype=np.uint64)
        prev = a["previous_rows"]
        has_prev = prev != NONE
        fail(np.all(prev[has_prev] < rows[has_prev]), "Invalid previous row order")
        p = prev[has_prev]
        fail(np.all(wid[p] == wid[has_prev]) and np.all(durable[p]), "Invalid own-window prefix")
        frontiers = a["query_frontiers"][has_prev]
        fail(np.all(start[p] <= frontiers) and np.all(frontiers < end[p]), "Invisible own prefix")
        targets = a["target_rows"]
        fail(np.all(prev[targets] != NONE), "Target has no BOS/prefix")
        # A target must never be its own visible conditioning key.
        fail(np.all(~durable[targets] | (a["query_frontiers"][targets] < start[targets])),
             "Target label leaks into its own query")
        return self


def _require(condition, message):
    if not condition:
        raise FormatError(message)


class Shard:
    """Read header/manifest at open; mmap index/data lazily on first selection."""

    def __init__(self, path, *, max_record_bytes=512 << 20, verify_index=False):
        self.path = Path(path)
        self.max_record_bytes, self._map, self._closed = max_record_bytes, None, False
        with self.path.open("rb") as stream:
            raw = stream.read(128)
            if len(raw) != 128:
                raise FormatError("Truncated shard header")
            h = HEADER.unpack(raw)
            (magic, major, minor, size, features, file_size, manifest_offset, manifest_size,
             index_offset, count, entry_size, alignment, data_offset, dataset, shard,
             header_crc, manifest_crc, index_crc, reserved) = h
            if (magic, major, minor, size, features, entry_size, alignment, reserved) != (
                    MAGIC, 1, 0, 128, 0, 64, 64, b"\0" * 4):
                raise FormatError("Unsupported shard header/version/features")
            if crc(raw[:112] + b"\0" * 4 + raw[116:]) != header_crc:
                raise FormatError("Header checksum mismatch")
            if file_size != os.fstat(stream.fileno()).st_size:
                raise FormatError("Truncated file or unexpected trailing bytes")
            if not 0 < manifest_size <= 1 << 20 or manifest_offset != 128:
                raise FormatError("Invalid/oversized manifest")
            expected_index_offset = align(128 + manifest_size)
            expected_data_offset = align(index_offset + count * 64)
            if index_offset != expected_index_offset or data_offset != expected_data_offset:
                raise FormatError("Invalid manifest/index layout")
            if data_offset > file_size:
                raise FormatError("Index exceeds file bounds")
            encoded = stream.read(manifest_size)
            if crc(encoded) != manifest_crc:
                raise FormatError("Manifest checksum mismatch")
        self.manifest = _json(encoded, "manifest")
        self.compatibility = _compatibility(self.manifest)
        self.dataset_id, self.shard_id = str(uuid.UUID(bytes=dataset)), str(uuid.UUID(bytes=shard))
        manifest_identity = (
            self.manifest.get("dataset_id"),
            self.manifest.get("shard_id"),
            self.manifest.get("sample_count"),
        )
        if type(manifest_identity[2]) is not int or manifest_identity != (
            self.dataset_id,
            self.shard_id,
            count,
        ):
            raise FormatError("Manifest/header identities or count disagree")
        self.count, self._index_offset, self._data_offset = count, index_offset, data_offset
        self._file_size, self._index_crc = file_size, index_crc
        if verify_index:
            view = memoryview(self._mapping())[index_offset:index_offset + count * 64]
            if crc(view) != index_crc:
                raise FormatError("Index checksum mismatch")

    def _mapping(self):
        if self._closed:
            raise ValueError("Shard is closed")
        if self._map is None:
            with self.path.open("rb") as stream:
                if os.fstat(stream.fileno()).st_size != self._file_size:
                    raise FormatError("Shard changed after opening")
                self._map = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        return self._map

    def entry(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        index_position = self._index_offset + index * 64
        entry = INDEX.unpack_from(self._mapping(), index_position)
        _, offset, stored, raw, rows, frames, windows, _, codec, flags, reserved, targets, tail = entry
        if (flags or reserved or tail or codec not in (0, 1, 2) or
                offset % 64 or offset < self._data_offset or offset + stored > self._file_size or
                not 64 <= raw <= self.max_record_bytes or not 0 < stored <= self.max_record_bytes or
                not 0 < frames <= rows or not 0 < windows <= rows or targets > rows):
            raise FormatError("Invalid index entry or resource limit")
        if codec == 0 and stored != raw:
            raise FormatError("Raw codec size mismatch")
        return entry

    def read(self, index, *, verify=True):
        entry = self.entry(index)
        _, offset, stored, raw, _, _, _, checksum, codec, *_ = entry
        block = memoryview(self._mapping())[offset:offset + stored]
        if verify and crc(block) != checksum:
            raise FormatError("Record checksum mismatch")
        try:
            if codec == 0:
                payload = block
            elif codec == 1:
                lz4 = _lz4()
                try:
                    payload = lz4.decompress(block, uncompressed_size=raw)
                except lz4.LZ4BlockError as error:
                    raise FormatError("Invalid bounded LZ4 record") from error
            else:
                decoder = zlib.decompressobj()
                payload = decoder.decompress(block, raw + 1)
                if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise FormatError("Invalid bounded zlib record")
        except (ValueError, zlib.error) as error:
            raise FormatError("Record decompression failed") from error
        return Sample(payload, entry)

    def release_mapping(self):
        # Live uncompressed Sample views retain the mapping independently. Dropping
        # our reference closes it once its final view is gone; no dangling buffers.
        self._map = None

    def close(self):
        self.release_mapping()
        self._closed = True


class Dataset:
    """Uniform trajectory sampling over all shards, with a bounded mmap LRU."""

    def __init__(self, paths, *, max_open_shards=32, verify_index=False):
        if max_open_shards < 1:
            raise ValueError("max_open_shards must be positive")
        self.shards = []
        self._ends = []
        self._lru = OrderedDict()
        self._closed = False
        self.max_open_shards = max_open_shards
        seen, compatibility, total = set(), None, 0
        try:
            for path in paths:
                shard = Shard(path, verify_index=verify_index)
                if shard.shard_id in seen:
                    shard.close()
                    raise FormatError("Duplicate shard ID: would duplicate samples")
                if compatibility is not None and shard.compatibility != compatibility:
                    shard.close()
                    raise FormatError("Incompatible tokenizer/control IDs/execution contract")
                seen.add(shard.shard_id)
                compatibility = shard.compatibility
                self.shards.append(shard)
                total += shard.count
                # Cumulative endpoints map a global sample index to its shard.
                self._ends.append(total)
                if verify_index:
                    shard.release_mapping()
            if not total:
                raise FormatError("Dataset has no trajectories")
        except BaseException:
            self.close()
            raise
        self.count = total

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if self._closed:
            raise ValueError("Dataset is closed")
        if not 0 <= index < self.count:
            raise IndexError(index)
        shard_index = bisect.bisect_right(self._ends, index)
        shard = self.shards[shard_index]
        self._lru.pop(shard_index, None)
        self._lru[shard_index] = None
        while len(self._lru) > self.max_open_shards:
            victim, _ = self._lru.popitem(last=False)
            self.shards[victim].release_mapping()
        start = self._ends[shard_index - 1] if shard_index else 0
        return shard.read(index - start)

    def sample(self, rng):
        # Uniform GLOBAL index. Picking shards uniformly would overweight small ones.
        return self[rng.randrange(self.count)]

    def epoch_indices(self, seed=0, epoch=0, *, rank=0, world_size=1, worker_id=0, num_workers=1):
        if (
            world_size < 1
            or num_workers < 1
            or not 0 <= rank < world_size
            or not 0 <= worker_id < num_workers
        ):
            raise ValueError("Invalid rank/worker partition")
        order = list(range(self.count))
        digest = hashlib.sha256(f"{seed}:{epoch}".encode()).digest()
        random.Random(int.from_bytes(digest, "little")).shuffle(order)
        # Disjoint ranks, then disjoint workers within a rank. No implicit padding.
        yield from order[rank + world_size * worker_id::world_size * num_workers]

    def close(self):
        for shard in self.shards:
            shard.close()
        self._lru.clear()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _column_bytes(values, dtype):
    code, width, numpy_dtype = DTYPES[dtype]
    if hasattr(values, "dtype"):
        import numpy as np
        values = np.asarray(values)
        if values.ndim != 1 or values.dtype != np.dtype(numpy_dtype):
            raise ValueError(f"Expected a one-dimensional {numpy_dtype} array")
        return values.tobytes(order="C")
    data = array.array(code, values)
    if data.itemsize != width:
        raise RuntimeError("Host array width differs from the wire format")
    if sys.byteorder != "little":
        data.byteswap()
    return data.tobytes()


def encode_sample(ident, columns, metadata=None):
    """Reference writer, not the compiler: accept already-lowered training arrays.

    NumPy is required for full validation here, not for memoryview-based reading.
    This writer intentionally validates rather than trusting arbitrary arrays.
    """
    ident = uuid.UUID(str(ident)).bytes
    if set(columns) != set(BY_NAME):
        raise ValueError("Writer requires exactly the 16 standard columns")
    encoded = {
        name: _column_bytes(columns[name], info[1])
        for name, info in BY_NAME.items()
    }
    rows = len(encoded["token_ids"]) // 4
    frames = len(encoded["frame_offsets"]) // 8 - 1
    windows = len(encoded["window_slots"]) // 4
    targets = len(encoded["target_rows"]) // 4
    counts = {"R": rows, "F+1": frames + 1, "W": windows, "Q": targets}
    if not 0 < frames <= rows or not 0 < windows <= rows or not 0 <= targets <= rows:
        raise ValueError("Invalid record dimensions")
    meta = b"" if metadata is None else canonical(metadata)
    directory_end = 64 + len(COLUMNS) * 32
    payload = bytearray(align(directory_end + len(meta)))
    RECORD.pack_into(
        payload,
        0,
        RECORD_MAGIC,
        1,
        0,
        64,
        ident,
        len(COLUMNS),
        len(meta),
        rows,
        frames,
        windows,
        targets,
        32,
        0,
    )
    payload[directory_end:directory_end + len(meta)] = meta
    for ordinal, (cid, (name, dtype, dimension)) in enumerate(COLUMNS.items()):
        value = encoded[name]
        count = len(value) // DTYPES[dtype][1]
        if count != counts[dimension]:
            raise ValueError(f"Wrong column length: {name}")
        offset = align(len(payload))
        payload.extend(b"\0" * (offset - len(payload)))
        column_position = 64 + ordinal * 32
        COLUMN.pack_into(
            payload, column_position, cid, dtype, 1, offset, count, len(value), 0
        )
        payload.extend(value)
    payload.extend(b"\0" * (align(len(payload)) - len(payload)))
    if len(payload) > NONE:
        raise ValueError("Record exceeds uint32 size limit")
    payload = bytes(payload)
    entry = (
        ident, 0, len(payload), len(payload), rows, frames, windows,
        0, 0, 0, 0, targets, 0,
    )
    Sample(payload, entry).validate()
    return payload, entry


def write_shard(path, samples, manifest, *, sample_count=None, codec="lz4"):
    """Atomically write one shard of (UUID, columns, optional_metadata) tuples.

    A sequence supplies its length; streaming iterators require sample_count.
    Existing files are never overwritten. Caller groups whole trajectories into
    shards; no whole-dataset buffering is needed.
    """
    path = Path(path)
    if codec not in CODECS:
        raise ValueError("Unknown codec")
    if path.exists():
        raise FileExistsError(path)
    count = len(samples) if sample_count is None else sample_count
    if type(count) is not int or count < 0:
        raise ValueError("sample_count must be a nonnegative integer")
    manifest = dict(manifest)
    manifest.update(format="ktremix", version="1.0", array_schema="ktremix.frontier-arrays/1",
                    sample_count=count)
    manifest.setdefault("dataset_id", str(uuid.uuid4()))
    manifest.setdefault("shard_id", str(uuid.uuid4()))
    _compatibility(manifest)
    manifest["dataset_id"] = str(uuid.UUID(manifest["dataset_id"]))
    manifest["shard_id"] = str(uuid.UUID(manifest["shard_id"]))
    dataset, shard = (uuid.UUID(manifest[name]).bytes for name in ("dataset_id", "shard_id"))
    encoded_manifest = canonical(manifest)
    if len(encoded_manifest) > 1 << 20:
        raise ValueError("Manifest exceeds 1 MiB")
    index_offset = align(128 + len(encoded_manifest))
    data_offset = align(index_offset + count * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w+b", dir=path.parent, prefix=".ktremix-", delete=False
        ) as stream:
            temp_name = stream.name
            stream.write(b"\0" * 128)
            stream.write(encoded_manifest)
            # Reserve the index before streaming records; each index entry is
            # filled as soon as its independently encoded trajectory is written.
            stream.seek(data_offset)
            seen, actual = set(), 0
            for actual, (ident, columns, metadata) in enumerate(samples, 1):
                if actual > count:
                    raise ValueError("More trajectories than sample_count")
                payload, entry = encode_sample(ident, columns, metadata)
                if entry[0] in seen:
                    raise ValueError("Duplicate trajectory UUID in shard")
                seen.add(entry[0])
                selected = CODECS[codec]
                if selected == 1:
                    stored = _lz4().compress(
                        payload, mode="fast", acceleration=1, store_size=False
                    )
                elif selected == 2:
                    stored = zlib.compress(payload, level=1)
                else:
                    stored = payload
                if len(stored) >= len(payload):
                    stored, selected = payload, 0
                offset = stream.tell()
                stream.write(stored)
                stream.write(b"\0" * (align(stream.tell()) - stream.tell()))
                end = stream.tell()
                entry = (entry[0], offset, len(stored), len(payload), *entry[4:7],
                         crc(stored), selected, 0, 0, entry[11], 0)
                stream.seek(index_offset + (actual - 1) * 64)
                stream.write(INDEX.pack(*entry))
                stream.seek(end)
            if actual != count:
                raise ValueError("Fewer trajectories than sample_count")
            # Materialize padding for empty shards too.
            end = max(stream.tell(), data_offset)
            stream.truncate(end)
            stream.seek(index_offset)
            checksum, remaining = 0, count * 64
            while remaining:
                block = stream.read(min(remaining, 1 << 20))
                if not block:
                    raise FormatError("Internal index write failure")
                checksum = zlib.crc32(block, checksum)
                remaining -= len(block)
            h = [
                MAGIC, 1, 0, 128, 0, end, 128, len(encoded_manifest),
                index_offset, count, 64, 64, data_offset, dataset, shard, 0,
                crc(encoded_manifest), checksum & 0xFFFFFFFF, b"\0" * 4,
            ]
            h[15] = crc(HEADER.pack(*h))
            stream.seek(0)
            stream.write(HEADER.pack(*h))
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard link publishes atomically AND refuses overwrites.
        os.link(temp_name, path)
    finally:
        if temp_name is not None:
            os.unlink(temp_name)
    return path


def demo_columns(seed=0):
    """Tiny synthetic storage fixture, not a curated training demonstration.

    Read-only context + available BOS; one model token activates a window, then
    a joint NOOP frame sleeps, external text wakes it, and EOS closes it.
    A replacement Available window survives. The sleep gap has no repeated rows.
    """
    return {
        "token_ids": [1, 10 + seed, 1, 100 + seed, 1, 3, 3, 200 + seed, 2, 3],
        "window_ids": [0, 0, 1, 1, 2, 1, 2, 0, 1, 2],
        "position_ids": [0, 1, 0, 1, 0, 2, 1, 2, 2, 1],
        "query_frontiers": [0, 1, 0, 2, 2, 3, 3, 3, 4, 4],
        "visible_from": [1, 2, 1, 3, 3, 0, 0, 4, 0, 0],
        "visible_until": [FOREVER, FOREVER, 5, 5, FOREVER, 0, 0, FOREVER, 0, 0],
        "previous_rows": [NONE, 0, NONE, 2, NONE, 3, 4, 1, 3, 4],
        "loss_weights": [0, 0, 0, 1, 0, .1, .1, 0, 1, .1],
        "row_flags": [DURABLE | BOS, DURABLE, DURABLE | BOS, DURABLE | MODEL,
                      DURABLE | BOS, MODEL | NOOP, MODEL | NOOP, DURABLE,
                      MODEL | EOS, MODEL | NOOP],
        "frame_offsets": [0, 5, 7, 10],
        "window_slots": [0, 1, 2],
        "window_open_frontiers": [0, 0, 3],
        "window_close_frontiers": [FOREVER, 5, FOREVER],
        "window_activation_frontiers": [0, 3, FOREVER],
        "window_flags": [READ_ONLY, INITIALLY_AVAILABLE, INITIALLY_AVAILABLE],
        "target_rows": [3, 5, 6, 8, 9],
    }


def demo_manifest(dataset_id=None, shard_index=0):
    return {"dataset_id": str(dataset_id or uuid.uuid4()), "shard_id": str(uuid.uuid4()),
            "shard_index": shard_index,
            "execution_contract": "ktremix.synthetic-demo/1",
            "tokenizer": {"sha256": hashlib.sha256(b"synthetic demo; not a real tokenizer").hexdigest(),
                          "special_token_ids": {"bos": 1, "eos": 2, "noop": 3}},
            "description": "Synthetic format fixture; do not use for actual training."}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="write two small synthetic shards (3 and 7 samples)")
    demo.add_argument("directory", type=Path)
    demo.add_argument("--codec", choices=CODECS, default="lz4")
    for command in ("sample", "inspect", "verify"):
        task = sub.add_parser(command)
        task.add_argument("paths", nargs="+", help="paths or quoted glob patterns")
        if command == "sample":
            task.add_argument("--count", type=int, default=5)
            task.add_argument("--seed", type=int, default=42)
            task.add_argument("--epoch", type=int)
            task.add_argument("--rank", type=int, default=0)
            task.add_argument("--world-size", type=int, default=1)
    args = parser.parse_args(argv)
    if args.command == "demo":
        dataset_id, offset = uuid.uuid4(), 0
        for i, count in enumerate((3, 7)):
            examples = [
                (uuid.uuid5(dataset_id, str(j)), demo_columns(j), {"synthetic": True})
                for j in range(offset, offset + count)
            ]
            path = args.directory / f"demo-{i:05d}.ktremix"
            write_shard(path, examples, demo_manifest(dataset_id, i), codec=args.codec)
            print(path)
            offset += count
        return
    paths = []
    for pattern in args.paths:
        matches = sorted(glob.glob(pattern))
        if not matches:
            parser.error(f"No matching shards: {pattern}")
        paths.extend(matches)
    with Dataset(paths, verify_index=args.command == "verify") as dataset:
        if args.command == "inspect":
            print(json.dumps({"trajectories": len(dataset),
                              "shards": [{"path": str(s.path), "manifest": s.manifest}
                                         for s in dataset.shards]}, indent=2))
        elif args.command == "verify":
            seen = set()
            for i in range(len(dataset)):
                sample = dataset[i].validate()
                if sample.uuid in seen:
                    raise FormatError("Duplicate trajectory UUID across shards")
                seen.add(sample.uuid)
                sample.metadata
            print(f"Verified {len(dataset)} trajectories in {len(dataset.shards)} shards")
        else:
            if args.count < 0:
                parser.error("count must be nonnegative")
            rng = random.Random(args.seed)
            if args.epoch is None:
                if args.rank or args.world_size != 1:
                    parser.error("rank/world-size partitioning requires --epoch")
                # Sampling with replacement is useful for randomized training.
                samples = (dataset.sample(rng) for _ in range(args.count))
            else:
                import itertools

                # An epoch visits each partitioned trajectory once, without padding.
                indices = dataset.epoch_indices(args.seed, args.epoch, rank=args.rank,
                                                world_size=args.world_size)
                samples = (dataset[i] for i in itertools.islice(indices, args.count))
            for sample in samples:
                summary = {
                    "uuid": sample.uuid,
                    "rows": sample.rows,
                    "frames": sample.frames,
                    "windows": sample.windows,
                    "targets": sample.targets,
                    "first_token_ids": list(sample.column("token_ids")[:12]),
                }
                print(json.dumps(summary))


if __name__ == "__main__":
    try:
        main()
    except (FormatError, OSError, RuntimeError, ValueError) as error:
        print(f"ktremix: {error}", file=sys.stderr)
        raise SystemExit(2)
