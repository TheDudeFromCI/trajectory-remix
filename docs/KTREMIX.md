# KTREMIX binary dataset format — 1.0 draft

This document defines a seekable, lightly compressed training-data format for
compiled multi-window trajectories. Its public reference implementation is
[`examples/ktremix_dataset.py`](../examples/ktremix_dataset.py). It includes a
reader, multi-shard sampler, validator, and small fixture writer. Python 3.11+ is
required; install `lz4` for the default compression and `numpy` for array views.

**Status:** implementable public draft for review. This PR does not implement the
new authoring compiler, wire the format into Remix's Export menu, or provide a
Kidra model/trainer. The reference writer accepts already-compiled arrays. File
layout and array semantics are specified here; the model execution contract is
identified explicitly in each manifest.

## 1. Design and access path

A `.ktremix` file is one immutable shard. It contains:

```text
128-byte file header
UTF-8 JSON manifest                    parsed once per shard
padding to 64-byte boundary
64-byte trajectory index entries       fixed-size, memory-mappable
padding to 64-byte boundary
independent trajectory record          raw or one compressed block
padding to 64-byte boundary
independent trajectory record
...
```

Each trajectory stays entirely in one shard. The index gives its byte offset,
stored/raw sizes, UUID and training dimensions. A training reader seeks directly
to one record and decompresses only that record. There is no whole-file ZIP/gzip
stream, token JSON parsing, pickle, dense attention matrix, or scan through earlier
trajectories on the sampling path.

Inside a record, training data is stored in contiguous typed columns. The reader
creates NumPy views over a single decompressed buffer. Uncompressed records can
be viewed directly through mmap. Relative array offsets are 64-byte aligned;
alignment of a decompressed Python allocation is not guaranteed to be 64 bytes.

Default writer policy is **LZ4 block compression**, retaining a raw record when
compression does not shrink it. Files can mix codecs record by record. The format
also defines zlib for a smaller, slower, standard-library-readable alternative.

Opening S shards reads S small headers/manifests, not their sample bodies. Global
uniform sampling uses a cumulative sample-count table, O(log S) shard selection,
an O(1) fixed-index lookup, and O(record size) decompression. The reference reader
bounds active mmap handles with an LRU. Long records still require reading and
decompressing the full trajectory; independently sampleable windows inside a
long record are deliberately not claimed by this version.

## 2. Conventions

- All integers are unsigned and **little-endian**, with no implicit C-struct padding.
- Float arrays use IEEE-754 binary32, little-endian.
- Offsets in the shard header/index are absolute file offsets. Column offsets are
  relative to the uncompressed trajectory record.
- `align64(x) = (x + 63) & ~63`. Padding written by this version is zero.
- UUIDs are 16 bytes in RFC UUID byte order (`uuid.UUID(...).bytes`, not bytes_le).
- CRC means standard IEEE CRC-32, as returned by `zlib.crc32`, **not CRC-32C**.
- `0xffffffff` means no previous row. `0xffffffffffffffff` means an unbounded end
  frontier or a window that never becomes Active. These sentinels are not real
  row indices/frontier values.
- JSON is UTF-8, an object at its root, with no duplicate keys or nonfinite numbers.
- Current major/minor version is 1/0. Readers must reject unsupported versions,
  codecs, nonzero reserved values and unknown required features/columns.
- Published shards are immutable. Writers publish completed temporary files
  atomically; never edit/truncate a shard while readers may have it mapped.

## 3. File header: 128 bytes

Python layout: `<8sHHI6QIIQ16s16sIII4s`.

| Byte offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | bytes[8] | magic | ASCII `KTREMIX` followed by NUL |
| 8 | u16 | major | 1 |
| 10 | u16 | minor | 0 |
| 12 | u32 | header_bytes | 128 |
| 16 | u64 | feature_bits | 0 in this version |
| 24 | u64 | file_bytes | Exact final length, including padding |
| 32 | u64 | manifest_offset | 128 |
| 40 | u64 | manifest_bytes | Manifest length, without padding |
| 48 | u64 | index_offset | `align64(128 + manifest_bytes)` |
| 56 | u64 | sample_count | Number of trajectory entries |
| 64 | u32 | index_entry_bytes | 64 |
| 68 | u32 | alignment | 64 |
| 72 | u64 | data_offset | `align64(index_offset + 64 * sample_count)` |
| 80 | bytes[16] | dataset_uuid | Identity of an export/dataset release |
| 96 | bytes[16] | shard_uuid | Unique identity of this shard |
| 112 | u32 | header_crc32 | CRC of all 128 bytes, with this field zeroed |
| 116 | u32 | manifest_crc32 | CRC of exact manifest bytes |
| 120 | u32 | index_crc32 | CRC of all index entries, without padding |
| 124 | bytes[4] | reserved | Zero |

Readers check header/manifest CRC and file bounds at open. Index CRC can be
checked once during validation rather than on every worker's startup. Requested
record CRCs are enabled by default in the reference sampler. CRCs detect accidental
corruption; they are not cryptographic authentication. A release can separately
publish whole-shard SHA-256 checksums.

## 4. Manifest

Example; UUID/hash values below are placeholders:

```json
{
  "format": "ktremix",
  "version": "1.0",
  "array_schema": "ktremix.frontier-arrays/1",
  "dataset_id": "00000000-0000-0000-0000-000000000001",
  "shard_id": "00000000-0000-0000-0000-000000000002",
  "sample_count": 1000,
  "shard_index": 0,
  "shard_count": 10,
  "execution_contract": "kidra.progressive-sleep/1",
  "tokenizer": {
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "special_token_ids": {"bos": 1, "eos": 2, "noop": 3}
  },
  "export": {"split": "train", "minimum_stage": "Ready", "include_stale": true, "metadata_trimmed": true}
}
```

Required: format, version, array_schema, dataset_id, shard_id, sample_count,
execution_contract and tokenizer. Identities/count must agree with the header.
Tokenizer SHA-256 is lowercase hex for the tokenizer serialization actually used
by the compiler; distribute or identify that exact tokenizer definition.
Special token IDs are u32 integers; BOS/EOS/NOOP must be distinct. Additional
dedicated protocol IDs may be included.

`execution_contract` is a nonempty, versioned model/compiler semantic identifier.
The example identifies the intended progressive-assignment/sleep contract, not
an implemented exporter in this PR. Fixture/demo contracts use distinct names
so they cannot be silently mixed into actual training data. The storage frontier
representation below carries visibility explicitly rather than guessing it from
an execution-contract name.

Recommended optional fields include shard_index/count, exporter/profile version,
compilation settings/seed, source-release ID, split, stage threshold, trim policy
and global statistics. Extra metadata has no executable meaning. No API keys,
provider credentials or arbitrary Python objects belong in this file.

Every shard is self-describing; no global sidecar is required for sampling. A
larger release may provide a catalog/checksum file to discover shards efficiently.
The reference reader rejects mixed tokenizer definitions/control IDs, array
schemas or execution contracts and duplicate shard UUIDs. Compatible separately
exported dataset UUIDs can be combined. Filename order and shard size must not
change trajectory-selection probabilities.

## 5. Trajectory index: 64 bytes per entry

Entry i is at `index_offset + 64*i`. Python layout: `<16sQ6IBBHIQ`.

| Offset | Type | Field |
|---:|---|---|
| 0 | bytes[16] | trajectory_uuid |
| 16 | u64 | record_offset, 64-byte aligned |
| 24 | u32 | stored_bytes, excluding file padding |
| 28 | u32 | uncompressed_bytes |
| 32 | u32 | row_count R |
| 36 | u32 | frame_count F |
| 40 | u32 | window_instance_count W |
| 44 | u32 | CRC of stored record bytes |
| 48 | u8 | codec |
| 49 | u8 | flags = 0 |
| 50 | u16 | reserved = 0 |
| 52 | u32 | supervised_row_count Q |
| 56 | u64 | reserved = 0 |

R, F and W are positive; F/W cannot exceed R. Q can be zero and cannot exceed R.
Rows/records must fit the u32 fields. The reference reader additionally limits
both stored/raw record size to 512 MiB by default; callers can choose a different
resource limit. Readers validate bounds before allocating/decompressing.

Rows and Q in the index support length bucketing or alternative sampling weights
without loading trajectory bodies. This specification's example sampler is
uniform over trajectories, not over tokens or loss weight. Zero-target samples
are representable; a trainer must explicitly handle or exclude them rather than
divide by a zero loss denominator.

Codecs:

| ID | Encoding |
|---:|---|
| 0 | Uncompressed record; stored_bytes equals uncompressed_bytes |
| 1 | Independent **raw LZ4 block**, no frame header or stored-size prefix; pass uncompressed_bytes to the decoder |
| 2 | Independent zlib-wrapped DEFLATE stream; reference writer uses level 1 |

Default is codec 1, falling back to 0 if the block is not smaller. Compression
level/acceleration is writer policy, not part of decoding. No record shares a
compression dictionary/history with another record. A CRC/size/decompression
failure is an error, not permission to silently substitute another sample.

## 6. Uncompressed record

### 6.1 Record header: 64 bytes

Python layout: `<8sHHI16s8I`.

| Offset | Type | Field |
|---:|---|---|
| 0 | bytes[8] | ASCII `KTSAMP` followed by two NUL bytes |
| 8 | u16 | major = 1 |
| 10 | u16 | minor = 0 |
| 12 | u32 | header_bytes = 64 |
| 16 | bytes[16] | trajectory_uuid, matching index |
| 32 | u32 | column_count K |
| 36 | u32 | metadata_bytes, possibly zero |
| 40 | u32 | R, matching index |
| 44 | u32 | F, matching index |
| 48 | u32 | W, matching index |
| 52 | u32 | Q, matching index |
| 56 | u32 | column_entry_bytes = 32 |
| 60 | u32 | reserved = 0 |

The column directory follows at byte 64. Optional metadata JSON follows at
`64 + 32*K`; it is not required or parsed on the training access path. Arrays
start no earlier than `align64(64 + 32*K + metadata_bytes)`.

### 6.2 Column directory

Python layout: `<HBBQQQI`, 32 bytes per entry.

| Offset | Type | Field |
|---:|---|---|
| 0 | u16 | column_id |
| 2 | u8 | dtype_id |
| 3 | u8 | flags: bit 0 means required, all other bits zero |
| 4 | u64 | byte offset relative to uncompressed record, multiple of 64 |
| 12 | u64 | element_count |
| 20 | u64 | byte_count, exactly count times dtype width |
| 28 | u32 | reserved = 0 |

No duplicate IDs, overlapping nonempty columns, or out-of-bounds regions are
allowed. Required standard columns have flags=1. Unknown optional columns may be
skipped using their declared bounds; unknown required columns/dtypes are errors.
Writers should order entries by column ID. Zero-length columns are permitted
where their declared dimension is zero.

Dtype IDs: **1=u8**, **3=u32**, **4=u64**, **6=f32**. Other IDs are reserved.

### 6.3 Required training columns

All are one-dimensional, contiguous, little-endian arrays. There is no compressed
varint/delta decoding step after record decompression.

| ID | Name | Type | Length | Meaning |
|---:|---|---|---|---|
| 1 | token_ids | u32 | R | Token/control ID for each row |
| 2 | window_ids | u32 | R | Local logical-window-instance index, less than W |
| 3 | position_ids | u32 | R | Window-local insertion/query position; NOOP does not advance durable position |
| 4 | query_frontiers | u64 | R | Causal frontier before constructing/predicting this row |
| 5 | visible_from | u64 | R | Inclusive frontier where a durable key becomes visible |
| 6 | visible_until | u64 | R | Exclusive frontier where replacement/closure removes that key |
| 7 | previous_rows | u32 | R | Latest earlier visible durable row in the same logical window, or NONE |
| 8 | loss_weights | f32 | R | Effective nonnegative finite weight after masking; zero means excluded |
| 9 | row_flags | u8 | R | Flags below |
| 10 | frame_offsets | u64 | F+1 | Half-open row ranges per compiled frame; first 0, last R, strictly increasing |
| 11 | window_slots | u32 | W | Physical slot for each logical window lifetime; reuse allowed |
| 12 | window_open_frontiers | u64 | W | Start of each physical/logical window lifetime |
| 13 | window_close_frontiers | u64 | W | End, or FOREVER when left open |
| 14 | window_activation_frontiers | u64 | W | When first text makes the window Active, or FOREVER if it stays Available |
| 15 | window_flags | u8 | W | Read-only/initial-Available flags below |
| 16 | target_rows | u32 | Q | Sorted row indices with strictly positive loss_weights |

Row flags: bit 0=DURABLE, bit 1=MODEL emission, bit 2=NOOP, bit 3=EOS, bit 4=BOS.
Other bits are zero. NOOP is MODEL and never DURABLE. BOS is durable, zero-loss
conditioning. BOS/EOS/NOOP flags are mutually exclusive. Closed-window EOS need
not become a visible key; the compiler encodes its actual interval/flags.

Window flags: bit 0=READ_ONLY, bit 1=INITIALLY_AVAILABLE, other bits zero.
INITIALLY_AVAILABLE describes the lifetime's initial state, not a claim that it
remains empty after activation. A READ_ONLY instance has no MODEL-emission rows.
An external row may be explicitly supervised by a chosen compilation profile;
supervision is independent of whether it was model-emitted at runtime.

Logical window instances remain distinct even if they reuse the same physical
slot. Their half-open lifetime intervals must not overlap when the slot is the
same. Initial Available BOS and later activation can share one instance. An
Available window still empty at sample end has activation=FOREVER. W includes
such instances; W is **not** peak Active capacity. Peak Active capacity excludes
Available instances and is separate compiler/export metadata.

### 6.4 Causal visibility and training

A durable memory row k is visible at frontier q exactly when:

```text
(row_flags[k] & DURABLE) != 0
and visible_from[k] <= q < visible_until[k]
```

The compiler already clips these intervals to window lifetime and replacement.
No dense attention matrix or per-query lists of every visible token are stored.
Frontiers are **local causal ordering coordinates, not wall-clock time**. They
linearize external insertions and prediction/commit boundaries; independent
same-frame model predictions share the appropriate pre-commit frontier. Their
own/current parallel targets become keys only after that frontier.

`query_frontiers` also applies to constructing memory representations, including
masked model content. A final attention mask cannot repair a memory representation
that was itself built with future information. For each durable row, its own
construction frontier precedes visible_from. For every target, previous_rows must
identify an earlier visible same-window prefix; **never feed its target token as
its own query input**.

Targets are `token_ids[target_rows]` with `loss_weights[target_rows]`; there is no
additional label shift. NOOP can carry loss without entering KV. Masked text can
remain durable conditioning. EOS/closure and replacement change visibility, not
historical row identity. Replacement's old-content cutoff and first new-token
visibility coincide as defined by the compilation contract; no visible empty
window is introduced. The preceding own-window input during a replacement must
be chosen from the retained visible prefix, not a removed row.

The progressive/sleep compiler must emit Available BOS/NOOP behavior and collapse
sleep correctly: retain the all-Active-NOOP decision frame and resume at context
change, without a wall-clock-sized run of idle model frames. Frame grouping,
frontiers and instance lifetimes preserve the resulting actual execution.
These semantics belong to compilation; the binary reader does not reconstruct
them from authored text, tick heuristics or a stored narrative.

Trainers can batch frontier queries and use sorted key intervals/window state to
build sparse/interval-aware attention on the accelerator. The reference offers
array views, not an optimized attention kernel. Concatenating arrays as ordinary
causal chat SFT is incorrect. Packed samples need separate sample identities,
visibility, positions and slot state; never allow cross-sample attention.

## 7. Sampling all shards

```bash
python -m pip install numpy lz4
python examples/ktremix_dataset.py demo /tmp/ktremix-demo
python examples/ktremix_dataset.py verify '/tmp/ktremix-demo/*.ktremix'
python examples/ktremix_dataset.py sample '/tmp/ktremix-demo/*.ktremix' --seed 42 --count 5
```

The demo is synthetic storage test data with a deliberately distinct execution
contract/tokenizer identity, not a useful SFT dataset.

Python training-loader usage (put the script on PYTHONPATH or copy/import it):

```python
from glob import glob
import random
from ktremix_dataset import Dataset

with Dataset(sorted(glob('train/*.ktremix')), max_open_shards=32) as data:
    rng = random.Random(42)
    for step in range(100):
        sample = data.sample(rng)              # uniform trajectory, with replacement
        a = sample.numpy()                     # zero-copy views after decompression
        targets = a['target_rows']
        labels = a['token_ids'][targets]
        weights = a['loss_weights'][targets]
        prefix_rows = a['previous_rows'][targets]
        query_frontiers = a['query_frontiers'][targets]
        # Pass these + memory columns/lifetimes/frame_offsets to the model's
        # multi-window training adapter. Fancy indexing above allocates only
        # the selected target arrays; a kernel can gather directly instead.
```

Global index selection uses the sum of shard counts. If one shard has 100 samples
and another 900, they receive 10%/90% of uniform-trajectory draws, not 50%/50%.
With-replacement draws may repeat; that is deliberate, not a uniqueness guarantee.

For a no-replacement epoch with distributed ranks and loader workers:

```python
with Dataset(paths) as data:                    # open inside each worker process
    for i in data.epoch_indices(seed=42, epoch=3, rank=rank, world_size=world_size,
                                worker_id=worker_id, num_workers=num_workers):
        sample = data[i]
        batch = sample.numpy()
```

Ranks partition one deterministic permutation, then workers partition their
rank's positions. Combined partitions cover the dataset exactly once, with no
implicit padding or dropping. Unequal shard sizes and uneven final partitions
are supported. A DDP trainer must explicitly choose how to handle unequal final
step counts; this example does not silently duplicate examples to equalize them.
The permutation uses O(N) indices per worker and Python's seeded shuffle; it is
an example sampling policy, not a cross-language wire-format guarantee. Uniform
draws avoid that permutation allocation.

Create readers inside workers rather than sharing live readers/RNGs across
processes. Keep CPU loading/decompression/prefetch separate from accelerator
work. Read-only NumPy views retain their buffer, including across reader closure.
To create mutable/pinned PyTorch staging tensors, copy explicitly before writing
or using a writable-tensor API; transferring to GPU necessarily copies data.

Stage and split filtering happen when selecting/exporting shards, not by guessing
from filenames. Use the intended training split. Sample metadata is optional and
is not a reliable substitute for an explicit split/release manifest. The `verify`
command checks index CRC, requested record CRCs, core array invariants, metadata
JSON and duplicate trajectory UUIDs across shards. Startup does not globally scan
every sample UUID or parse every optional metadata block.

The planned Export menu includes an **Include stale trajectories** option,
enabled by default. This is separate from the minimum-stage filter: stale
signoffs do not change a trajectory's stage or trigger a new review. Record the
selection in the optional export manifest. This draft does not wire the new
options into the production exporter.

For a runnable two-layer, approximately 50k-parameter model that generates raw
sources, compiles, samples and trains against these shards, see the
[training demo](KTREMIX-TRAINING-DEMO.md).

## 8. Metadata, validation and extension

Metadata trimming removes optional narrative, generation/repair logs and verbose
signoffs, never required arrays, tokenizer identity, window-instance lifetimes,
labels or effective loss. Human window/event names and source maps can be stored
as optional record metadata or optional typed extension columns; training uses
numeric instance IDs directly.

The reference validates file/record bounds, identities, column types/counts and
non-overlap on requested records. `Sample.validate()` additionally checks frame
boundaries, target selection, finite losses, own-prefix visibility, no self-label
leakage, NOOP/BOS rules, slot reuse and lifetimes. It is not proof that an upstream
compiler faithfully represented its authored scenario: compiler parity tests
against source causal views remain necessary. Missing context is not repaired
by the loader. New required semantics require an explicit supported version or
feature; unknown metadata is passive and unknown optional columns can be skipped.

Writers must finish header/index/checksums before publication. The reference
writer spools one record at a time, validates it, and publishes with a same-folder
hard link that refuses to overwrite an existing path. Other exporters can use an
equivalent atomic publication strategy. Empty shards are representable; an empty
combined dataset cannot be sampled. Failed writes must not appear as complete
`.ktremix` files.

This format stores complete trajectories. Storage sharding and model compute
chunking are separate concerns. Randomly taking a suffix as an independent sample
loses its prefix; detaching cached activations changes a full-gradient objective.
Any later independently seekable subtrajectory format must explicitly describe
prefix state/recomputation, rather than silently change that training objective.

## 9. Reference performance experiment

Reproduce with [`scripts/benchmark_ktremix.py`](../scripts/benchmark_ktremix.py):

```bash
python scripts/benchmark_ktremix.py --rows 4096 --trajectories 256 --draws 1000 \
  --directory /tmp --output ktremix-benchmark.json
```

One run on Linux x86-64, Python 3.12.14, NumPy 2.3.5: 256 synthetic trajectories
in unequal shards, 4,096 rows each, 1,000 random selections, warm local OS page
cache. Binary reads include record CRC, decompression and all NumPy column views;
only the first token is consumed after view creation. This is loader readiness,
**not GPU transfer, full-array consumption, cold/remote I/O or training throughput**.
The JSON baseline also has direct record offsets and zlib-1 compression, so it
does not pay for a sequential scan. It parses equivalent row fields without
building training arrays, giving JSON a favorable lower-bound comparison.

| Layout | Total bytes | Samples/s | Median load ms | p95 ms |
|---|---:|---:|---:|---:|
| Raw binary/mmap | 59,704,576 | 17,960.8 | 0.0534 | 0.0676 |
| **LZ4 binary (default)** | **25,503,040** | **6,524.6** | **0.1410** | **0.1945** |
| zlib-1 binary | 13,245,312 | 2,852.9 | 0.3391 | 0.3921 |
| Indexed row JSON + zlib-1 | 21,956,992 | 233.7 | 4.2402 | 4.4442 |

LZ4 is the proposed default balance: this run used about 43% of raw-binary bytes
and loaded about 2.3x as many samples/s as zlib binary. Uncompressed mmap remains
available when decompression costs more than extra I/O/storage. These are one
synthetic local run's observations, not a claim of universally fastest storage.
Choose codecs, shard sizes, record sizes and worker counts against real hardware.

Run the conformance/reference tests with:

```bash
python tests/test_ktremix_format.py
```
