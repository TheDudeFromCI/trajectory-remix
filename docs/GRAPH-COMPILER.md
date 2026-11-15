# Compiler and preview

The compiler turns a dependency graph into the row-by-row training form: which tokens appear
when, what each token may see, and what it is trained to predict. You only need this page to
understand **Tick history** previews, export behavior and compile errors. Authoring never calls
it.

The compiler reads the [source format](GRAPH-SOURCE.md). It doesn't execute tools, call models,
change sources or settings, or calculate stored metrics. Its output feeds [Export](EXPORT.md) and
the [KTREMIX](KTREMIX.md) format. Install its dependencies with `pip install -e '.[export]'`.

## Using it

Compilation is always explicit: a **Tick history** preview in the studio, an export job, or code:

```python
from trajectory_remix.graph import GraphSource
from trajectory_remix.graph.compiler import GraphCompiler

source = GraphSource.model_validate(source_json)
compiler = GraphCompiler(
    settings.tokenizer_profiles[profile_id], settings=settings, active_limit=8, seed=17
)
arrays, identity = compiler.compile(source)
preview = compiler.preview(source, max_frames=500)
```

- `compile` returns the arrays [KTREMIX](KTREMIX.md) stores, plus a small identity/debug map.
- `preview` returns a bounded list of frames (`tick`, `inputs`, `outputs`), a `truncated` flag
  and a cache key covering compiler version, source hash, profile fingerprint, capacity and seed.
- Both accept a `cancelled()` callback. Compilation has a `max_rows` guard (default one million)
  and preview also limits frames. Cancellation raises `Cancelled`, and failures raise
  `CompileError`.
- Only the intrinsic source matters, so sources that differ only in metadata compile identically
  for the same profile, capacity and seed.
- **Errors block compilation, warnings don't.** An incompatible tokenizer also blocks it.

In previews, a window reference shows as a typed annotation, and its token ID is `None` until a
real compile resolves it. It is not a generated token.

## The tokenizer

A tokenizer profile is validated only when you use it. Control spellings and IDs must match the
serialized tokenizer, and the vocabulary is never extended. Literal text is encoded with
added-token matching turned off, and text that would produce a control token or lose content is
rejected. A byte-covering tokenizer can therefore carry control-looking strings as plain data.

Events never merge tokens or masks with each other. BOS is zero-loss context, an implicit NOOP uses
the source's `noop_loss_weight`, and all other framing inherits its event's supervision.

### Wire layout (`kidra.graph-compiler/1`)

| Piece | Tokens |
| --- | --- |
| Window header | CTX begin, compact JSON `{slot, purpose, reasoning_effort}`, CTX end |
| Tool call | tool-begin control, a JSON string tool ID, a literal colon (and tool-end / response-begin / response-end as the flags request) |
| Chat | MSG begin, JSON string speaker, colon, content, MSG end |
| Window reference | the window-ref control followed by **eight lowercase hex digits** of a u32, one ordinary token per digit |
| Close window | the EOS label, which is never a context key |

The fixed-length reference lets you refer to windows that open later or have closed, even though
physical slots are reused. It needs a tokenizer that encodes each hex digit as one ordinary token.
Referencing a window that never opens is an error.

## Timing

Timing exists only in compiled output. The compiler assigns ticks like this:

- **Agent events** stream one token per tick, and agent-created window headers stream the same
  way. **External events** deliver instantly or one token per tick, per their `delivery` mode.
  External headers appear atomically before the first body fragment. Chat is atomic.
- **External completion** can enable generation in the same tick. `chain_immediately: false` moves
  the next external event to the next input phase. **Agent completion** enables dependents no earlier
  than the next tick.
- There are no authored offsets or sandbox latency, and a dependency-only source cannot express
  an arbitrary wall-clock delay.

## Windows, visibility and sleep

- **Available windows.** There is always one BOS-only *Available* instance. A first agent header
  token activates it, and a new reserve appears after the shared commit. Simultaneous first writes
  are ordered by event ID, and external openings skip the reserve. Physical slots come from a
  reproducible RNG seeded by the export seed and trajectory UUID, so they vary across trajectories
  and can be reused. `active_limit` is the peak number of Active windows, with one extra slot for
  the reserve.
- **What each prediction sees.** Every insertion has a causal frontier. All predictions in a frame
  share a pre-commit frontier, so simultaneous targets can't see each other.

### Replacement and closing

- **Closing:** EOS closes a window after the shared pass. Peers still see its full prior content,
  and no later pass can see it. EOS and NOOP never become context keys.
- **Replacement:** the BOS and header and the window's identity stay. The old body expires at the
  same frontier where the first replacement token becomes visible, so there is no model pass over
  an empty body. Later streamed tokens accumulate normally.

Visibility intervals alone don't make it safe to keep hidden-state copies of removed text.
Anything that built on removed content must be rebuilt, which the training demo does.

### Idle windows and sleep

Writable Active windows and Available predict NOOP when idle, and read-only windows count as NOOP
without model rows. When **every** window predicts NOOP in one decision, execution **sleeps** until
external context changes, with no repeated idle passes. Open windows at the end of a sample are
allowed.

## Developers

Export services are described in [Export](EXPORT.md). The
job service pins snapshots, calls these services and runs them asynchronously ([Jobs](GRAPH-JOBS.md)).
Caller-side preview behavior (debounce, caching, stale-response fencing) lives in the
[studio](GRAPH-STUDIO.md#preview-modes), and the compiler holds no global preview or run state.

```bash
python -m pytest -q tests/test_graph_foundation.py tests/test_graph_compiler.py \
  tests/test_graph_export.py tests/test_ktremix_format.py tests/test_ktremix_training.py
```

The tests use small deterministic fixtures covering CRC and bounds, phases and allocation, framing
and masks, typed references, metadata independence, split families, expiry and cancellation,
two-layer context isolation and a single optimizer update. The old attention predicate is used only
as a test oracle. Training convergence, local-model quality and 25k-stored / 20-active performance
are user-machine checks ([Acceptance](ACCEPTANCE.md)).
