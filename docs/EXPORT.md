# Token export and training contract

Exports are JSONL. Each line contains one time-bounded chunk of one sample.
Chunks never split a tick across records, so concurrently generated windows
remain in one logical decision group. Chunk size counts ticks, not flattened
tokens: a tick may contain many external tokens and multiple window targets.

## Record fields

| Field | Meaning |
|---|---|
| `format` | `kidra-token-chunk/1` |
| `sample_id`, `chunk_index` | Stable trajectory and ordered chunk number. |
| `chunk_count`, `sample_row_count` | Completeness checks, including missing final chunks. |
| `tokenizer_sha256`, `special_tokens` | Exact tokenizer identity and native BOS/EOS/NOOP IDs. |
| `tick_start`, `tick_end` | Inclusive global tick interval. |
| `requires_prefix_through_row` | Last earlier row needed for context. -1 in the first chunk. |
| `windows` | Logical window ID/type, numeric `slot`, resolved `header`, and open/close tick. Close null means still open at sample end. |
| `window_slots` | Mapping from logical IDs to numeric slots, shared by headers, `{WINDOW:id}` references, and the training adapter. |
| `rows` | All token/decision records, including excluded observations and implicit NOOPs. |
| `token_ids` | Row-aligned token/target IDs. |
| `labels` | Token ID where loss is enabled; -100 otherwise. No further target shifting. |
| `loss_mask`, `loss_weights` | Row-aligned inclusion and effective weight (mask already applied). |
| `attention_rule`, `cache_policy` | External-first visibility and durable-only KV policy. |

Each row includes `index`, `tick`, `phase` (A/B/D), `window`, `token_id`,
`position`, `durable`, `loss_mask`, `loss_weight`, `op`, `event`, and
`input_rank`. `position` is the window-local insertion position; NOOP does not
advance it. `event` identifies the source replay event; `implicit` identifies an
omitted idle target. `input_rank` orders same-tick external insertions.

Do not interpret the flat `token_ids` array as a standard causal chat sequence.
BOS and automatic headers are context, external rows insert context, and D rows are independent model
decisions. NOOP is a prediction target but never a durable memory token.

At opening, `op: "open"` inserts BOS, followed immediately by `op: "header"`
rows in phase A. Header rows are durable with zero loss and consecutive local
positions. They are available to same-tick B/D queries and leave KV with the
rest of their window on closure. `defaults.window-headers: false` omits them.
Headers consume no ticks, but increase context length and shift local positions.

Authored `{WINDOW:id}` markers resolve before tokenization, so their numeric
replacement determines token counts, loss segment boundaries and anchor timing.
Slots follow first declarations in canonical replay-key/list order, independent
of resolved ticks. Implicit tool windows may be listed first in `windows` while
having a later slot; consumers must use `slot` rather than the array index.
The reference adapter falls back to array indices for older exports without slots.

## Exact attention visibility

`compiler.visible_to(query, key, windows)` is the executable reference:

1. Only durable rows can be memory keys.
2. A key's window must be open at the query tick. An EOS closes after phase D,
   so its prior window context remains visible to other decisions that tick,
   then becomes unavailable at the next tick.
3. Earlier ticks are visible, subject to window lifetime.
4. A phase-D query sees same-tick phase-A BOS/headers and phase-B external insertions.
   It sees no phase-D output from any window, including its own target.
5. A supervised phase-B external query sees phase-A BOS/headers and earlier phase-B
   insertions (`input_rank`). It cannot read its target or later external text.
6. No future tick is visible.

Loss exclusion never removes a key from conditioning. Conversely, a NOOP target
may carry loss but is not a conditioning key. Causal masks and loss masks are
separate tensors.

## Reference adapter

```python
from trajectory_remix.training import read_plans, query_batch, to_torch

for plan in read_plans("token-chunks.jsonl"):
    # Debug/reference path for short samples. Build sparse masks in production.
    batch = query_batch(plan, plan["rows"])
    tensors = to_torch(batch, device="cuda")  # optional torch dependency
```

`query_batch` returns durable memory tokens and per-target query window,
position, tick, last visible own-window token, labels, weights, and a dense
`attention_allowed` matrix. It includes only positive-weight targets. It never
uses a target token as that target's query input. A production Kidra decoder
must build query states from the appropriate prior window state; the adapter's
previous token IDs are metadata, not a replacement for that architecture.
The model's memory representations must also have been constructed causally.
An output-layer mask alone cannot undo leakage in earlier layers.

PyTorch's attention APIs differ in whether boolean true means allowed or
blocked; this export defines **true = allowed**. Convert as needed for the
specific attention operation. The adapter has no standard-trainer assumptions.

## Packed full-gradient chunking

A sample may have an instant prompt, two generated windows, a long-running tool
stream, idle reasoning, and then a warning-triggered reaction. The exported
rows retain those global times; chunking does not move the warning or flatten
the generated streams into sequential conversations.

For full gradients:

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

The `kidra_step` above is intentionally architecture-specific pseudocode.
There is no bundled Kidra trainer. Use the reference predicate to test any
batched implementation against tick replay before optimizing it.

A chunk is a compute/storage boundary, not an independent training sample.
Dropping earlier chunks loses conditioning; detaching the cache changes the
objective to truncated backpropagation. Preserve the graph through the whole
sample or use graph-preserving checkpoint/recomputation. Do not perform an
optimizer step between chunks of the same full-gradient sample. Denominator
is the total effective target weight, not token count or per-chunk mean.

When packing multiple samples into one batch, prohibit cross-sample attention,
reset lifecycle/ticks/window slots at each sample boundary, and retain each
sample's tokenizer identity. Reject mismatched tokenizers. Padding is a trainer
choice and must have zero loss and zero attention visibility.
