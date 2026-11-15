# Graph sampler and metrics

The graph studio uses the supplied Kidra `sample_one(matrix, seed,
world_group_id, overrides)` implementation unchanged. The three original files,
source attribution and exact hashes are in
[`sampler_originals`](../trajectory_remix/graph/sampler_originals/README.md).
The seed-137 example is reproduced exactly. No distribution experiment is part
of installation, the adapter, or correctness tests.

## Shared editor contract

All sampler UI designs use these authenticated REST endpoints and the same
`SamplerService`. There is no alternative sampler or adaptive balancing loop.

| Endpoint | Request/result |
| --- | --- |
| `GET /api/v1/sampler` | Editor snapshot: configuration, settings revision/commit, supported override choices |
| `PUT /api/v1/sampler` | `{expected_revision, matrix, metric_options}`; CAS update and synchronized definitions |
| `POST /api/v1/sampler/proposals` | `{seed?, world_group_id?, overrides?}`; 201 immutable persisted snapshot |
| `GET /api/v1/sampler/proposals/{uuid}` | Full historical snapshot |
| `POST /api/v1/sampler/runs` | `{requests:[{seed?,world_group_id?,overrides?}],instructions:{...}}`; 202 durable run job |

Matrix and registry configuration lives in versioned Project Settings as
`sampler_configuration`. The `matrix` and `metric_registry` references are derived
from that same payload on validation, including through the ordinary settings
editor. The bundled matrix is the effective default until first edit; reads do
not write settings. Matrix options, objectives, scenario conditions, directives,
and sampled `scenario` fields provide the shared human-readable projection.
Metric options customize labels, units, optional numeric target keys and
absolute/relative tolerance bands; categories always derive from the matrix.
Custom annotation metrics may be added with metric options. No tolerance is
assumed. Changing configuration only changes future snapshots.

Supported pins are `focus.family`, `lifecycle`, `complexity`, `domain`,
`user_competence`, `user_patience`, `user_involvement`, `user_tone`. Invalid or
incompatible pins return 422, without retry, relaxation or resampling. The
original constraint errors are configuration diagnostics. Arbitrary future
matrix constraint systems are not formally proven by the original validator.

A proposal snapshot contains a storage `id` UUID, full `proposal`, full-payload
`payload_digest`, optional explicit `family_id`, and `frozen` editor inputs.
The original `definition_id` is retained inside the proposal as provenance; it
excludes pins and sampler version and must never be a uniqueness key. Repeated
identical draws still get separate storage UUIDs. Run creation freezes all
requests up front, including attempts admitted later, using the same editor
snapshot. The existing run service freezes instructions, proposals and settings;
retry/resume consumes those saved inputs. Generation drivers remain a separate
integration: a run pauses explicitly until a driver is installed.

For source creation, use `SamplerService.metadata(snapshot)` to attach the
original proposal and `targets` independently of observed `metrics`. The driver
must retain that metadata through variants/repairs. Only an explicitly supplied
same-scenario world ID becomes `metadata.family_id`; the sampler's provisional
seed-based world name does not imply a dataset family. Copy that known identity
for related variants/repairs, never infer it from broad domain/template/run seed.
The existing exporter resolves explicit/Auto/missing splits, preserves known
families, blocks conflicting explicit family splits, balances project ratios,
warns/excludes missing independently of Auto, and writes separate split shards.
It resolves against the snapshot without editing source split metadata.

## Observations and population reporting

The existing Dataset Metrics dialog uses saved records in the current search,
with proposal coverage, unavailable/stale counts and selected trajectory values.
Unsaved drafts are excluded. Calculate Missing Metrics starts the existing
search-wide bulk job with frozen metric definitions. It retains normal source
CAS/locks, pause/cancel, warnings and one settled browser Undo action. Only
absent/null values are filled; stale or manual values are retained for explicit
revision-checked editing. Metrics refresh never tokenizes or compiles.

| Endpoint | Behavior |
| --- | --- |
| `POST /api/v1/metrics/population` | `{query:{text?,stage?,split?,tool?},population:"all"|"accepted"}`; streaming source snapshot aggregation |
| `GET /api/v1/metrics/trajectories/{id}` | Definitions, observations/status/drift, original targets |
| `PUT /api/v1/metrics/trajectories/{id}/{key}` | `{expected_revision,value,origin,profile_fingerprint?,compiler_version?}`; explicit annotation |
| `POST /api/v1/metrics/calculate-missing` | `{query:{...}}`; 202 bulk job with frozen configuration |

Accepted coverage means saved stage Candidate (1) or higher. It uses the original
sampler coverage report over saved proposal targets: categorical marginals,
cross-tabs, multi-label frequencies, numeric targets, behavior coverage and mixed
matrix version warnings. These are intentions, never proof of observed behavior.
Unknown actuals never become zero. Non-proposal sources count in population
availability totals but not sampler coverage denominators.

Source calculators distinguish raw event fragments, authored windows, and unique
logical tool-call IDs. None measures semantic coordination behavior, task count,
peak Active capacity or token exposure. Semantic categories/outcomes require
explicit manual annotation. Peak Active and token measures require explicit
export observations with profile fingerprint and compiler version; the application
does not automatically publish compiler measurements or perform semantic tagging.
Source observations record origin, intrinsic source revision/hash, and definition
revision. Changes to source or definition mark the saved observation stale;
metadata-only edits preserve its currency. Per-metric drift is available only
for numeric observations with a saved numeric target and an explicit tolerance:
`abs(actual-target) > max(absolute, abs(target)*relative)`. Optional `target` names
a key in frozen `targets.numeric_targets`; no implicit equivalence between raw
fragments, logical events and peak capacity is introduced.

## Correctness checks

```bash
python -m pytest -q tests/test_graph_sampling.py tests/test_graph_studio.py \
  tests/test_graph_jobs.py tests/test_graph_foundation.py tests/test_graph_compiler.py \
  tests/test_graph_export.py
```

Fixtures use only a few deterministic proposals and tiny sources. Original byte
and canonical hashes, reference equality, pins, identity collisions, immutable
snapshots, frozen targets, registry synchronization/CAS, provenance, unknowns,
staleness, missing-only calculation/history, family conflicts and split balancing
are checked. `python tests/run_graph_frontend.py` checks the retained studio.
No benchmarks, full training, scale tests or live LLM calls are run. User-machine
25k-stored/20-active, live Flow and training validation remain separate gates.

Generation and manual Review & Repair now use the shared [event Flow](GRAPH-GENERATION.md).
See [local-LLM validation](GRAPH-LOCAL-LLM.md) for user-machine quality checks.

Editor validation additionally rejects duplicate identities, all-zero choice
groups and dangling known rule/forced/tag/lifecycle references. The exact supplied
originals remain byte-for-byte unchanged, including their pre-existing unmatched
`request_approval` condition. This preserves sampler provenance and is not a
proof that every arbitrary custom constraint combination is satisfiable.
