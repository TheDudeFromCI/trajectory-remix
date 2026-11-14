# 04 — Exact sampler adapter, linked metrics registry and split families

Read [CONTEXT](CONTEXT.md), contracts **§3 and §8**, and the long register's
"Supplied sampler contract and inspection" section. Requires 01 and the exact
input artifacts. Suggested branch: `feat/sampler-metrics-registry`.

## Input dependency — do not reconstruct from summaries

Original files are not currently present. Restore them from the user's actual
attachments/source copies before adapter implementation. Recorded identities:

| File / representation | SHA-256 |
|---|---|
| `kidra_sampler.py` bytes | `e00a66f5be1131f83f390ff8784edc6d2207202262cea5a1a51b833b582b175a` |
| `kidra_sampling_matrix.v1.json` bytes | `59e30228558b9db8f19e73b782be6070589a1dc6a241555a400286fcdc1bb853` |
| canonical matrix JSON | `5e41e190ff9349f57411461ba5626126ff9466a9b27da3c4fea8449c5ed28ff7` |
| `kidra_example_definition.json` bytes | `952a3d5c9468df8225e54a689305e576c0099f8388c67d29543dc32629d120c7` |

Earlier inspection reproduced the seed-137 example with saved overrides. That
is historical evidence, not current files. If unavailable, request the originals;
only the adapter is blocked, not the manual studio foundation. Do not invent the
43 behavior/22 axis constraint system from its prose description.

## Scope

- Preserve the supplied `sample_one(matrix, seed, world_group_id, overrides)` and
  Monte Carlo behavior. Version/hash configuration and persist the full proposal.
- `definition_id` excludes overrides/version and is NOT a uniqueness key. Allocate
  storage UUIDs/full payload digests; retain its original ID as provenance.
- Manual pinning uses supported overrides; incompatible pins are configuration
  errors, not silent resampling/relaxation. Freeze inputs at run start, including
  later trajectories admitted to that run. Historical targets never change.
- Link sampler definitions and customizable metric registry; matrix UI edits
  update corresponding definitions/config for future runs. No separate diverging
  metric ontology or adaptive coverage controller.
- Actual metric provenance: origin, definition/source revision and export profile
  identity where relevant. Unknown remains unavailable. Explicit missing-value
  calculation; no compile-on-metrics-refresh or speculative semantic tagging.
- Tolerances per metric; no universal 20%/1. Distinguish raw fragment count from
  logical behavior and exact export-only peak/token measures.
- Same-scenario variants/repairs retain family identity; broad domain/template/run
  seed is not a family. Keep target labels distinct from observed labels.
- Export split policy integrates 02: explicit + Auto/missing, group-aware ratio
  balancing, independent missing warnings/exclusion, separate split shards.
- Reuse existing metrics panel/query/coverage/unavailable reporting, optionally
  adding a search bar; do not create a duplicate panel.

## Interface for dependent work

Expose a stable matrix/registry editor API, proposal snapshot DTO, override
validation and human-readable scenario/metric projections. Three UI prototypes
must use this SAME backend and source definition; no variant-specific sampler
logic. Generation 06 consumes the immutable proposal, not reinterpreted tags.
Make source-versus-export metrics and stale/unavailable results explicit.

## Small checks

Reference example equality, a few deterministic override cases/digest identity
checks, invalid pins, registry synchronization, original target retention, unknown
actuals/provenance, family conflicts, Auto versus missing and tiny split-balanced
fixtures. No distribution/scale/throughput experiment or real LLM call.

Deliver a focused draft PR with exact artifacts/license/source attribution as
appropriate, adapter/registry docs and clear readiness for all three UI branches.
Do not merge or manufacture absent inputs.
