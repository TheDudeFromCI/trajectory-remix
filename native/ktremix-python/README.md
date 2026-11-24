# KTREMIX native library

Compile Trajectory Remix graph JSON and raw text into KTREMIX 1.0 training shards.
The Rust `ktremix` crate contains the compiler, diagnostics, wire format, corpus
converter and sampler. The separately installable `ktremix-native` Python
package wraps that crate and requires no Trajectory Remix installation.

From this checkout, install with `python -m pip install './native/ktremix-python[numpy]'`.
Rust 1.89 or newer is needed for a source build. Rust consumers can depend
on `ktremix` by path or the repository's Git revision. The workspace contains
the package sources for distribution.

## Compile or inspect a graph

Use a tokenizer profile exported from Trajectory Remix, with its Hugging Face
tokenizer JSON in `definition` and named control IDs in `special_tokens`.

```python
import json
from pathlib import Path

from ktremix_native import Compiler, dry_run

profile = json.loads(Path("profile.json").read_text())
source = Path("trajectory.json").read_text()
structural = dry_run(source)  # Tokenizer-free
compiler = Compiler(profile, active_limit=32, seed=7)
report = compiler.dry_run(source)
if not any(item["severity"] == "error" for item in report["diagnostics"]):
    compiled = compiler.compile(source)
    token_ids = compiled["columns"]["token_ids"]
```

Use `dry_runs(iterable)` or `compiler.dry_runs(iterable)` for bounded batches.
Pass the same optional settings snapshot to both calls for project tool and
reasoning-level checks. Diagnostics always include warnings. Errors block
compilation. A successful tokenizer-backed dry run returns total rows
(including controls), supervised target rows, peak active windows (excluding
Available), and peak visible rows at a prediction frontier.

Compile multiple files with bounded native parallelism. The iterator is pulled
up to `queue_size` paths at a time. Results are written in input order, and an
error discards the unpublished shard.

```python
paths = sorted(Path("trajectories").glob("*.json"))
compiler.write_files(
    "training/curated.ktremix",
    iter(paths),
    sample_count=len(paths),
    workers=8,
    queue_size=16,
)
```

Rust callers use `Compiler::compile`, `Compiler::report`,
`Compiler::structural_report`, and `Compiler::compile_files`. To write directly
to disk, feed batches to `shard::CompileWriter` and finish the writer.

## Convert raw corpus text

Supply UTF-8 text files with one document per file, or UTF-8 JSONL with one
`{"id": "...", "text": "...", "provenance": {...}}` document per line. Rust
callers can also supply an iterator of `corpus::Document` records. Python callers
can pass a document iterator to `convert_documents`, for example from batches
read with PyArrow. Prepare domain sampling and train/validation splits before conversion.
Convert each split to its own output directory.

```python
shards = compiler.convert_corpus(
    ["documents.jsonl"],
    "training/corpus",
    provenance={"source": "my-corpus", "revision": "v1", "split": "train"},
    workers=8,
    samples_per_shard=128,
    single_window_fraction=0.25,
    retained_sections=2,
)
```

The converter preserves tokenizer order within each document and prefers
paragraph breaks when splitting windows. Each new section starts after the
preceding section's text has committed. Earlier windows stay visible for the
configured number of later sections, and independent documents can run
concurrently as distractors. Natural text gets ordinary next-token loss.
Control and NOOP loss are separately configurable. Long documents split at
`max_tokens_per_document_part` so each sample has bounded size. Shards and
sample IDs are deterministic for the same input and configuration. Use
`resume=True` to compare existing shard metadata and continue from the same
ordered input. Use distinct source revisions in `provenance` for distinct corpora.

## Sample shards

```python
from ktremix_native import Dataset

dataset = Dataset(
    ["training/curated.ktremix", "training/extra.ktremix"],
    batch_size=8,
    shard_weights=[0.75, 0.25],
    without_replacement=True,
    seed=42,
    decode_workers=4,
)
for batch in iter(dataset.next_batch, []):
    arrays = [sample.numpy() for sample in batch]
```

Default sampling gives each record the same probability. When specified, shard
weights control shard selection and zero excludes a shard. Non-repeating mode
yields each sample UUID at most once per epoch, even across shards containing
the same sample. Call `reset_epoch(epoch)` to begin a reproducible new epoch.
A final batch can be short. NumPy arrays are read-only views of the native
sample buffer and keep it alive. The Rust reader exposes borrowed
little-endian column byte slices from `shard::Sample`.
