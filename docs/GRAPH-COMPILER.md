# Graph compilation, preview and export

The opt-in `trajectory_remix.graph.compiler` and `.export` services consume the
[graph source contracts](GRAPH-SOURCE.md). They do not read replay sources,
execute tools, call models, mutate source/settings, or calculate stored metrics.
Graph reads/edits remain tokenizer-free. The existing dashboard's legacy export
route is unchanged; these services are ready for the graph studio and durable job
service to call. No placeholder asynchronous job endpoint is supplied.

Install the exporter dependencies with `pip install -e '.[export]'`.

## Explicit compilation and preview

```python
from trajectory_remix.graph import GraphSource
from trajectory_remix.graph.compiler import GraphCompiler

source = GraphSource.model_validate(source_json)
compiler = GraphCompiler(settings.tokenizer_profiles[profile_id],
                         settings=settings, active_limit=8, seed=17)
arrays, identity = compiler.compile(source)
preview = compiler.preview(source, max_frames=500)
```

`compile` returns the required [KTREMIX frontier columns](KTREMIX.md) and a small
identity/debug map. The compiler consumes intrinsic fields only. Metadata-free
sources produce identical arrays with the same profile, capacity and seed.
Structural errors and incompatible tokenizers block compilation; warnings do not.
Settings, when supplied, enable current tool-catalog/effort diagnostics.

`preview` returns a bounded list of `Frame(tick, inputs, outputs)` dataclasses,
a `truncated` flag and a cache key covering compiler version, intrinsic hash,
profile fingerprint, capacity and seed. Each emission identifies its event,
logical window, token/control pieces, supervision, opening and replacement.
References retain typed `(logical_window, hex_digit_index)` annotations in the
visual schedule, avoiding unbounded lookahead for a future allocation. Only
compiled arrays resolve them to physical token IDs. The token ID in such an annotated preview piece is `None` until compilation
resolves it; it is not a generated token or a slot.

Preview shares the scheduling core with export and writes no artifacts. A caller
should apply its client-local trailing debounce (default 500 ms), cache by this
key, and reject responses whose source hash/profile no longer matches selection.
The compiler holds no global preview/export selection or mutable run state.
`frames(source)` offers the same incremental visual schedule for explicit callers.
Graph mode, generation, review and source metrics must not call these APIs.

Both methods accept a `cancelled()` callback. `compile` has a configurable
`max_rows` guard (default one million); preview additionally limits frames.
Cancellation raises `Cancelled`, and compilation failures raise `CompileError`.

## Tokenization and wire grammar

The profile is validated by the tokenizer runtime when explicitly selected.
Dedicated control spellings/IDs must agree with the serialized tokenizer.
Vocabulary is never extended. Literal text is encoded with added-token matching
and postprocessing disabled; literal encoding that produces a lifecycle/control
ID or discards nonempty content is rejected. A byte-covering tokenizer supports
literal control-looking strings. Independent events never merge tokens or masks.
BOS is always zero-loss conditioning; implicit NOOP uses the source default loss.
All other framing inherits the originating event's whole-event supervision.

Compiler version `kidra.graph-compiler/1` uses:

- Header: CTX begin, compact JSON `{slot,purpose,reasoning_effort}`, CTX end.
- Tool begin: tool-begin control, JSON string tool ID, literal colon. Independent
  end flags emit tool-end/response-end; responses use response-begin.
- Chat: MSG begin, JSON string speaker, colon, literal content, MSG end.
- Typed reference: window-ref control followed by eight lowercase u32 hex digits,
  encoded one ordinary token per digit. A reference therefore needs a tokenizer
  that encodes each hex digit independently as one ordinary token. Its fixed
  length permits references to future/closed windows despite reusable slots.
  Referencing a window that never opens is a compilation error.
- Close window: EOS label, never a durable context key. No text scanning controls
  submission or closure.

Headers on agent-created windows stream one token per generation tick. External
headers populate atomically before the first body fragment. Agent events stream
one token per tick; external text/responses deliver all tokens instantly or one
body token per tick according to the actual source `delivery.mode` contract.
Chat is atomic. There is no authored timing offset or sandbox latency.

External completion can enable generation in the same tick. A receiving external
event's `chain_immediately=false` moves it to the next input phase. Agent
completion always enables dependents no earlier than the next tick. Input chains
follow dependencies, including dependency-ordered same-window appends.

## Allocation, visibility and sleep

One BOS-only Available instance exists. A first agent header token activates it
and creates a new reserve after the shared generation commit. Concurrent first
agent writes are staggered by stable event-ID order. External populated openings
bypass the reserve. Allocation uses a reproducible RNG derived from export seed
and trajectory UUID; it varies across trajectories and may reuse vacant slots.
The configured capacity is peak Active windows; one extra physical slot is kept
for Available. Logical IDs never reopen. Unordered writers remain errors.

Every input insertion has a construction/commit frontier. All predictions in a
frame share a pre-commit frontier, so simultaneous targets cannot see one another.
EOS closes after that shared pass: peers can see the closing window's full prior
content, and no next-pass query can see it. EOS and NOOP never become KV keys.

Replacement retains BOS/header and window identity. The old body expires at the
same frontier where the first replacement token becomes visible; the replacement
row's direct own-window prefix is the retained header. There is no observable
empty-body model pass. Subsequent streamed tokens accumulate normally.

Visibility intervals alone do not authorize retaining hidden-state copies of
removed content. Consumers must rebuild/invalidate surviving representations
that incorporated removed keys. The tiny demo intersects every memory layer's
construction mask with each query's current visible context and rebuilds it;
this checks closure/replacement isolation without a detached prefix cache.

Writable Active windows and Available predict NOOP when idle. Read-only windows
count as NOOP without model rows. One all-NOOP decision puts execution to sleep;
only an external context change wakes it. The scheduler emits no repeated idle
passes or authored wait intervals. Dependency-only sources cannot express
arbitrary wall-clock delays. End-of-sample open windows are allowed.

## Split-aware temporary artifacts

```python
from trajectory_remix.graph.export import ArtifactService, ExportOptions

artifacts = ArtifactService()  # expires old/abandoned exports on startup
artifact_id, manifest = artifacts.build(
    records, settings, compiler=compiler,
    options=ExportOptions(trajectories_per_file=100, metadata="remove_all"))
with artifacts.lease(artifact_id, "download.zip") as stream:
    # Stream to the requesting client's download response while holding the lease.
    consume(stream)
```

`records` accepts `GraphSource` objects/dicts or `SourceRecord` snapshots. Sources
and settings are copied before selection. Provided revision/commit identities,
intrinsic hashes, settings hash, profile, compiler, capacity and allocation seed
are pinned in manifests. The caller must obtain a consistent snapshot through
the real job/store service; this API does not acquire production job ownership.

Defaults: compiled KTREMIX, minimum stage Ready (3), include stale, include missing
splits, 256 whole trajectories/file, and keep targets plus measured metrics.
`minimum_stage=None` explicitly includes metadata-free sources. This service
recognizes optional metadata `stage`, `stale`, `split` and `family_id`. Split
values are Train/Development/Test or lowercase equivalents; Auto is intentional.
Missing split warns. Families are indivisible; conflicting explicit family splits
block export. Automatic families are assigned against remaining ratio deficits,
accounting for explicit counts. Ratios may be approximate. Snapshot assignments
never rewrite source splits. The split seed is recorded separately from allocation.

Projection policies: `full`, `remove_all`, `measured_metrics` (keep `metrics`),
`all_metrics` (keep `metrics` and `targets`). Resolve split/grouping before
projection. Intrinsic data is always retained in raw JSONL; compiled arrays are
unchanged by projection. Each shard contains only one resolved split.

All outputs live in unique, mode-0700 export directories under `/tmp`, including
raw JSONL, KTREMIX, exact tokenizer/profile, manifest and ZIP. Downloads become
available only after the complete marker is atomically published. Each shard has
its own CRC/bounds checks; the bundle manifest includes file lengths/SHA-256.
Failed/cancelled writers remove their directory, including finished partial shards.
The service never exposes partial results as successful exports.

Completed artifacts expire 24 hours after completion. `expire()` is suitable for
the durable service's periodic sweep; initialization also sweeps overdue and
abandoned exports. Writer/download locks protect active files from cleanup.
OS removal may happen earlier. A durable job must keep its compact status and
artifact reference outside this temporary tree. No scheduling or REST workers
are started by constructing this service.

## Correctness and integration gates

```bash
python -m pytest -q tests/test_graph_foundation.py tests/test_graph_compiler.py \
  tests/test_graph_export.py tests/test_ktremix_format.py tests/test_ktremix_training.py
```

These are small deterministic fixtures, including format CRC/bounds, phase and
allocation rules, framing/masks, event boundaries, typed references, metadata
independence, split families, fake-clock expiry/cancellation, two-layer context
isolation and one optimizer update. The legacy attention predicate is used only
as a test oracle, without reintroducing replay sources into the graph path.

The graph studio and durable job service must integrate these APIs. Full model
training/convergence, local-LLM acceptance and the 25k-stored/20-active performance
gates remain user-machine checks. No benchmarks or live model acceptance belong
in this correctness suite. See the [updated training harness](KTREMIX-TRAINING-DEMO.md).
