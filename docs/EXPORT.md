# Export and training

This page explains how to export trajectories as training data and how that data
must be consumed. The first half is for everyone exporting; the second half is for
people writing a trainer.

## Exporting

```bash
python -m trajectory_remix --dataset samples.sqlite \
  --export token-chunks.jsonl --chunk-ticks 64
```

- Only trajectories marked **Ready (3)** are exported, in both the CLI and the
  dashboard. Pending and Validated samples are skipped.
- **Every included trajectory must compile**, or the export fails. The output file is
  replaced only on success, so you never get a partial file.
- Output is JSONL: each line is one **chunk** of one trajectory. Chunks are cut on tick
  boundaries and never split a tick, so windows that generate together stay together.
  `--chunk-ticks` counts ticks, not tokens.
- Exports carry the tokenizer's SHA-256 fingerprint. Marker timings depend on the
  tokenizer, so re-export after a [tokenizer migration](GETTING-STARTED.md#change-a-projects-tokenizer).
- **File → Export → Current Trajectory** is different: it downloads the selected
  *source JSON* (including drafts), not training tokens.

## Record fields

| Field | Meaning |
| --- | --- |
| `format` | `kidra-token-chunk/1` |
| `trajectory_uuid` | Immutable trajectory key and slot-allocation seed. |
| `slot_policy`, `max_window_slots` | Allocation policy version and the project's slot budget. |
| `sample_id`, `chunk_index` | Trajectory ID and ordered chunk number. |
| `chunk_count`, `sample_row_count` | Completeness checks, such as detecting a missing last chunk. |
| `tokenizer_sha256`, `special_tokens` | Exact tokenizer identity and the BOS/EOS/NOOP IDs. |
| `tick_start`, `tick_end` | Inclusive global tick range. |
| `requires_prefix_through_row` | Last earlier row needed as context (-1 in the first chunk). |
| `windows` | Each window's logical ID and type, numeric `slot`, resolved `header`, and open/close ticks (close is null if still open at the end). |
| `window_slots` | Logical ID to numeric slot map shared by headers, `{WINDOW:id}` references and the adapter. |
| `rows` | Every token or decision record, including excluded observations and implicit NOOPs. |
| `token_ids` | Row-aligned token and target IDs. |
| `labels` | The token ID where loss is enabled, else -100. Already aligned; do not shift again. |
| `loss_mask`, `loss_weights` | Row-aligned inclusion and effective weight (mask already applied). |
| `attention_rule`, `cache_policy` | External-first visibility and durable-only KV policy. |

Each row has `index`, `tick`, `phase` (A, B or D), `window`, `token_id`, `position`
(the window-local insertion position, which NOOPs do not advance), `durable`,
`loss_mask`, `loss_weight`, `op`, `event` (the source event), `implicit` (an omitted
idle target) and `input_rank` (order of same-tick external insertions).

Use `slot` rather than array position to identify windows: an implicit tool window can
be listed first yet have a later slot. The reference adapter falls back to array
indices only for older exports without slots.

## Reading the data correctly

Do **not** treat `token_ids` as an ordinary causal chat sequence.

- BOS and headers are context. External rows insert context. D rows are independent
  model decisions made from a shared snapshot.
- NOOP is a prediction target but never a durable memory token.
- When a window opens, `op: "open"` inserts BOS, followed by `op: "header"` rows in
  phase A. Headers are durable, carry no loss, have consecutive local positions, are
  visible to same-tick B and D queries, and leave KV when their window closes. They
  use no ticks but lengthen context and shift local positions.
  `defaults.window-headers: false` removes them.
- `{WINDOW:id}` markers are resolved before tokenization, so their numeric spelling
  affects token counts, loss boundaries and anchor timing.
- Slot numbers come from the project's `uuid-hash-v1` policy and the trajectory UUID,
  visiting windows in canonical replay-key and list order, independent of ticks.

## Exact attention visibility

`compiler.visible_to(query, key, windows)` is the executable reference:

1. Only durable rows can be memory keys.
2. A key's window must be open at the query tick. EOS closes after phase D, so the
   window stays visible to other decisions that tick and disappears the next tick.
3. Earlier ticks are visible, subject to window lifetime, and future ticks never are.
4. A phase-D query sees same-tick phase-A BOS/headers and phase-B external insertions,
   but no phase-D output from any window, including its own target.
5. A supervised phase-B query sees phase-A BOS/headers and earlier phase-B insertions
   (by `input_rank`), but not its own target or later external text.

Loss exclusion never removes a key from conditioning, and a NOOP target may carry loss
without being a key. Keep causal masks and loss masks as separate tensors.

## Reference adapter

```python
from trajectory_remix.training import read_plans, query_batch, to_torch

for plan in read_plans("token-chunks.jsonl"):
    # Debug/reference path for short samples. Build sparse masks in production.
    batch = query_batch(plan, plan["rows"])
    tensors = to_torch(batch, device="cuda")  # optional torch dependency
```

`query_batch` returns durable memory tokens and, for each positive-weight target, its
query window, position, tick, last visible own-window token, label, weight, and a dense
`attention_allowed` matrix, where **true means allowed**. Convert if your attention API
uses the opposite convention. It never uses a target token as its own query input.

A production Kidra decoder must build query states from the appropriate prior window
state; the adapter's previous-token IDs are metadata, not an architecture. Memory
representations must also be built causally, because an output-layer mask cannot undo
leakage in earlier layers. There is no bundled trainer.

## Chunking for full-gradient training

Chunking keeps each tick's global time. A sample might have an instant prompt, two
generated windows, a long tool stream, idle reasoning and a warning-triggered reaction,
and none of that is flattened into sequential conversations.

```python
optimizer.zero_grad()
attached_cache = new_window_caches()
loss_sum, weight_sum = 0, 0

for chunk in ordered_sample_chunks:
    for tick_group in group_by_tick(chunk.rows):
        # A: allocate windows, insert BOS and automatic header with no loss.
        # B: causally insert external content. Normally excluded from target loss.
        #    If an observation is supervised, compute its logit before insertion.
        # C: predict all model-owned targets from ONE after-B snapshot.
        #    Do not expose any generated current-tick target to another query.
        # D: commit generated durable tokens and apply EOS closures.
        #    NOOP contributes a target but never enters KV.
        losses, weights = kidra_step(tick_group, attached_cache)
        loss_sum += (losses * weights).sum()
        weight_sum += weights.sum()
    # Keep the prefix graph attached across chunks. Do not detach KV here.

(loss_sum / weight_sum.clamp_min(1)).backward()
optimizer.step()
```

`kidra_step` is architecture-specific pseudocode. Use the reference predicate to test
any batched implementation against a tick-by-tick replay before optimizing.

Guidelines:

- A chunk is a compute and storage boundary, **not** an independent training sample.
  Dropping earlier chunks loses conditioning, and detaching the cache turns the
  objective into truncated backpropagation. Keep the graph through the whole sample
  (or use graph-preserving checkpointing), and take no optimizer step between chunks
  of one sample.
- Normalize by total effective target weight, not token count or a per-chunk mean.
- When packing several samples into a batch, forbid cross-sample attention, reset
  lifecycle, ticks and window slots at each sample boundary, keep each sample's
  tokenizer identity, and reject mismatched tokenizers. Padding must have zero loss
  and zero attention visibility.
