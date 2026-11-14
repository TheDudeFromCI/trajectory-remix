# Dataset metrics

Dataset metrics show what your dataset covers and how its trajectories are shaped, so
you can spot gaps before training. Open **View → Dataset Metrics** in the dashboard,
or use the MCP tools listed below.

## Using the metrics window

1. Choose a **source population** (see [Populations](#populations)).
2. Press **Calculate pending metrics**. Compilation runs as a background job on the
   server, and browsing or normal startup never triggers it. Graphs reuse cached
   values.
3. Watch progress (processed and total, elapsed time, current sample). **Stop after
   current sample** cancels cleanly.

The job survives closing the panel or disconnecting an MCP client, and starting it
again joins the running job. After a cancellation or restart, a new start resumes from
the per-sample caches already completed.

**Graphs** cover primary domains, terminal outcomes, same-tick model concurrency,
external streaming activity, raw loss allocation, tool presence and window categories.
The **distribution selector** adds token and control counts, supervised and masked
targets, duration, window use, visible context, tool failures, pooled window lengths,
idle runs and tool latency. Each distribution reports count and missing, mean,
population standard deviation, min and max, p50/p90/p95/p99, and a histogram. Bin
edges include the left value and exclude the right, with underflow and overflow
shown. Pooled windows, runs and calls are labeled separately from per-sample
numbers. A zero denominator gives null, never a zero fraction.

## Fixing alerts

**Alerts & repairs → Open metadata** selects the affected sample and opens the
structured editor for coverage and provenance or observed labels. Unrelated and nested
properties are preserved, edits sync with Source JSON, and they use the same
immediate-save, conflict and undo behavior as other edits. Unknown labels stay unknown
until independently reviewed, and the database upgrade invents no metadata. Annotations
travel with normal JSON/JSONL import and export.

The [metrics guide](../trajectory_remix/guides/metrics.md) (MCP
`get_authoring_guide(topic="metrics")`) documents the metadata fields, vocabulary and
the observer query workflow.

### Backfilling old datasets

For sources without coverage fields, query the **All** population first, then call
`get_metric_annotation_context`. It returns definitions, current annotations, paged
trace evidence and dataset or scenario guidance in one bounded response, with no
numeric compilation required. Excerpts are marked and come with commands to fetch the
rest. Traces can support domain, outcome and behavior labels. Missing provenance or
family/split policy stays unresolved until the owner supplies it or lineage records
it. Alerts are never cleared by invented history or blanket train/original labels.

## MCP tools

`get_dataset_metrics`, `refresh_dataset_metrics`, `get_dataset_metrics_status`,
`cancel_dataset_metrics_refresh`, `get_dataset_metric_distribution`,
`query_dataset_metrics` and `get_metric_annotation_context`. They work over stdio and
Streamable HTTP, and the dashboard's REST routes use the same handlers. A refresh
returns a job ID immediately, so poll the status tool. Prefer
`population="accepted_train_originals"` for observer gap analysis, and `population="all"`
when finding legacy samples that lack split or original metadata.

## Populations

| Population | Includes |
| --- | --- |
| **All current source samples** (default) | Every sample, including rejected and unfinished ones. Missing identity or split fields appear as alerts. |
| **Accepted sources** | Samples in the Ready state (3). |
| **Accepted train originals** | Ready samples with an explicit train split and an explicit original-source flag. The coverage rules run **only** here. |

Missing identity or split fields are never silently assigned to accepted training
data. Unknown semantic labels stay in their eligible denominator, and requested tags
never establish a correct example.

## How to read the numbers

- Content is counted from real tokenizer IDs and compiler rows. BOS, EOS and NOOP are
  counted apart from content, and headers count as external content.
- Open windows alone do not prove simultaneous generation, so concurrency is counted
  from actual decisions. External-stream counts exclude instant insertions.
- Visible context uses the phase-D decision frontier: same-tick generated tokens are
  not visible to other same-tick decisions, replaced content disappears, and closed
  windows stop providing context.
- The compiler allows one open/close lifetime per logical window. Repeated tool
  invocations are counted independently.
- Raw loss weights come before trainer normalization and are not measured gradient
  shares.

**Scope.** The bundled [launch YAML](../trajectory_remix/coverage_launch.yaml) comes from
the Project Kidra Dataset Metrics & Observer launch proposal v0.1. Its **source** rules
are advisory diagnostics: support gates, unknown-label counts, conditional denominators
and distinct-family counts. Loading it does not activate the proposal's controller,
automatic generation, admission gates or sampling changes, and all numeric targets are
hypotheses. This is a measurement and annotation foundation, not the full observer
system. Family and split conflicts are flagged from IDs and names alone, without
reading test content, and an exact normalized replay hash flags duplicate structures
(it is not a near-duplicate judge).

**Not available:** export concurrency or context quotas, export manifests, trainer and
hardware logs, historical generation cost, semantic reaction, dependency and closure
annotations, and near-duplicate judgments. Response latency and last-needed-evidence
ticks are not guessed from the last write.

## Cache details

Opening a schema 3, 4 or 5 project adds a disposable `trajectory_metrics` table using
the usual upgrades. Existing data, tokenizer, revisions and tables are untouched, and
older schema-5 readers can ignore the table.

Each cached row belongs to a trajectory UUID (deleted with it). Its key includes the
canonical source hash, tokenizer and control tokens, window policy, tool definitions,
metric definitions and launch configuration, so edits, imports, undo, redo, tokenizer
migration or code changes hide stale values automatically. A calculation publishes a
result only if the source hash still matches. It holds no write lock while compiling
and releases read snapshots between samples. Coverage and review-only annotation edits
can reuse numeric results, while changes to executable source, speaker names, clock,
defaults, tokenizer or tools cannot. The cache is derived data and can be discarded
safely.
