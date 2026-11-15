# Sampler and metrics

This page explains how the sampler turns your matrix into scenario proposals, how a proposal's *targets*
differ from *measured* results, and how the metrics are stored and reported. For tuning, see the
[Sampler Matrix workbench](SAMPLER-WORKBENCH.md). For the Dataset Metrics window, see [Metrics](METRICS.md).

## The sampler

Remix uses the supplied Kidra `sample_one(matrix, seed, world_group_id, overrides)` **unchanged**. The three
original files, their source and their exact hashes are in
[`sampler_originals`](../trajectory_remix/graph/sampler_originals/README.md), and the seed-137 example is
reproduced exactly. There is no alternative sampler and no adaptive balancing loop.

The matrix and metric registry live in project settings as `sampler_configuration`. The bundled matrix is the
default until your first edit, and reading it never writes settings. Changing it only affects future snapshots.
Metric options set labels, units, optional numeric target keys and tolerance bands. Categories always derive
from the matrix, and you can add custom annotation metrics. No tolerance is assumed.

### Pins

You can pin `focus.family`, `lifecycle`, `complexity`, `domain`, `user_competence`, `user_patience`,
`user_involvement` and `user_tone`. An invalid or incompatible pin returns 422, with no retry, relaxing or
resampling.

### Proposals

A proposal **snapshot** has a storage `id` (UUID), the full `proposal`, a `payload_digest` of the whole payload,
an optional explicit `family_id`, and the `frozen` editor inputs. Repeated identical draws still get separate
IDs. The proposal's original `definition_id` is kept as provenance only. It leaves out pins and the sampler
version, so never use it to deduplicate.

A run freezes all its requests up front (including attempts admitted later) using the same snapshot.

### Family IDs

Only an explicitly supplied same-scenario world ID becomes `metadata.family_id`. The sampler's provisional
seed-based world name doesn't imply a family. Copy a known family ID across related variants and repairs, and
never infer it from a broad domain, template or run seed. Export keeps families together, balances splits and
blocks conflicting explicit family splits ([Export](EXPORT.md#splits-and-families)).

## Targets are not results

- **Targets** are the sampler's intentions, stored with the proposal in `targets`.
- **Metrics** are observations about the finished source, stored separately in `metrics`.

A passed target never proves the behavior happened, and an unknown actual is never treated as zero. The
generation driver must keep the proposal and targets through variants and repairs
(`SamplerService.metadata(snapshot)` produces them).

## Observations

Source calculators count three different things, which are never treated as equivalent: raw event fragments,
authored windows, and unique logical tool-call IDs. None of them measure semantic coordination behavior, task
count, peak Active capacity or token exposure.

- **Semantic** categories and outcomes need explicit manual annotation. Nothing tags them automatically.
- **Peak Active and token** measures need explicit export observations carrying a profile fingerprint and compiler
  version. The app doesn't publish compiler measurements back to sources.
- Each observation records its origin, the source revision and hash, and the definition revision. A changed
  source or definition marks it **stale**, while metadata-only edits keep it current.
- **Drift** is reported only for numeric observations with a saved numeric target and an explicit tolerance:
  `abs(actual − target) > max(absolute, abs(target) × relative)`. An optional `target` names a key in the
  frozen `targets.numeric_targets`.

**Calculate Missing Metrics** runs a search-wide bulk job with frozen metric definitions. It fills only absent or
null values, keeps source revision checks, locks, Pause and Cancel, and leaves one Undo action. It never
tokenizes, compiles or calls a model.

**Coverage** counts only accepted sources (stage Candidate (1) or higher) and uses the original sampler's
report over saved proposal targets: categorical marginals, cross-tabs, multi-label frequencies, numeric targets,
behavior coverage and warnings about mixed matrix versions. Sources without a proposal count toward availability
totals, but not coverage denominators.

## REST routes

All authenticated under `/api/v1`.

| Route | Purpose |
| --- | --- |
| `GET /sampler` | Editor snapshot: configuration, settings revision and commit, supported pins. |
| `PUT /sampler` | `{expected_revision, matrix, metric_options}`, checked and synchronized. |
| `POST /sampler/proposals` | `{seed?, world_group_id?, overrides?}`. 201 with an immutable snapshot. |
| `GET /sampler/proposals/{uuid}` | A historical snapshot. |
| `POST /sampler/runs` | `{requests:[…], instructions:{…}}`. 202 with a durable run job. |
| `POST /metrics/population` | `{query:{text?, stage?, split?, tool?}, population:"all"\|"accepted"}`. Streaming aggregation. |
| `GET /metrics/trajectories/{id}` | Definitions, observations, status, drift and original targets. |
| `PUT /metrics/trajectories/{id}/{key}` | `{expected_revision, value, origin, profile_fingerprint?, compiler_version?}`. An explicit annotation. |
| `POST /metrics/calculate-missing` | `{query:{…}}`. 202 with a bulk job. |

## Validation rules

Saving a matrix rejects duplicate identities, all-zero choice groups, and dangling references from rules,
forced values, tags and lifecycles. The exact original files stay byte-for-byte unchanged, including their
pre-existing unmatched `request_approval` condition. That preserves provenance, but it is not a proof that every
custom constraint combination is satisfiable.

```bash
python -m pytest -q tests/test_graph_sampling.py tests/test_graph_studio.py \
  tests/test_graph_jobs.py tests/test_graph_foundation.py tests/test_graph_compiler.py \
  tests/test_graph_export.py
```

These use a few deterministic proposals and tiny sources. No distribution experiments, benchmarks, training or live
model calls run, and the 25k-stored / 20-active, live-Flow and training gates remain separate.
