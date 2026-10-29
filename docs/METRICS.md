# Dataset metrics

Open **View → Dataset Metrics**. Choose a source population and press **Calculate
pending metrics** for a first pass. Compilation runs as a server-owned background
job outside the server event loop. **Stop after current sample** pauses further work. Refreshing
graphs reuses cached values; browsing and ordinary startup do not calculate them.
The initiating request returns immediately, with pollable processed/total counts,
elapsed time and current sample. Work survives a disconnected MCP client or closed
panel; repeated starts join the same active job. After cancellation or server
restart, another start resumes from completed per-source caches.

Graphs cover primary domains, terminal outcomes, same-tick model concurrency,
external streaming activity, raw loss allocation, tool presence and window
categories. The distribution selector adds token/control counts, supervised and
masked targets, duration, window use, visible context, tool failures, pooled
window lengths, idle runs and tool latency. Statistics include count/missing,
mean, population standard deviation, min/max, p50/p90/p95/p99 and histograms.
Bin edges are inclusive on the left and exclusive on the right; underflow and
overflow remain visible. Pooled window/run/call statistics are labeled separately
from per-source statistics. Zero denominators return null, never a zero fraction.

**Alerts & repairs → Open metadata** selects the affected sample in replay and
opens the structured coverage/provenance or observed-label editor. These controls
preserve unrelated and nested properties, synchronize with Source JSON, and use
the existing immediate-save, conflict and undo/redo behavior. Unknown labels stay
unknown until independently reviewed. Source annotations are portable in existing
JSON/JSONL imports and exports. No metadata is fabricated during database upgrade.

The [MCP metrics guide](../trajectory_remix/guides/metrics.md) documents the exact
metadata contract, vocabulary and observer query workflow. Both stdio and
Streamable HTTP expose `get_dataset_metrics`, `refresh_dataset_metrics`,
`get_dataset_metric_distribution`, `query_dataset_metrics`,
`get_dataset_metrics_status`, `cancel_dataset_metrics_refresh` and
`get_metric_annotation_context`; the dashboard REST
routes call the same validated command handlers. Agents can request the guide
with `get_authoring_guide(topic="metrics")`.

For old sources without coverage fields, query the **all** population first.
`get_metric_annotation_context` prepares the definitions, existing annotations,
paginated trace evidence and dataset/scenario guidance in one bounded response.
It requires no numeric compilation. Explicitly marked excerpts provide complete
source/guidance continuation commands. Trace review can support domain/outcome
and behavior labels; absent historical provenance or family/split policy remains
unresolved until supplied by the owner or recorded lineage. Alerts are not cleared
by fabricated history or blanket train/original assignments.

## Interpretation and limits

The default **All current source samples** inventory includes rejected and
unfinished sources. **Accepted sources** means the existing Ready state (3).
The PDF's coverage rules run only for **Accepted train originals**, requiring
Ready, an explicit train split and an explicit original-source flag. Missing
identity/split fields are alerts in the all-source inventory; they cannot be
silently assigned to accepted training data. Unknown semantic labels stay in
their eligible denominator, and requested tags never establish a correct example.

The bundled [launch YAML](../trajectory_remix/coverage_launch.yaml) is copied from
the supplied Project Kidra Dataset Metrics & Observer launch proposal v0.1.
Its **source** rules are advisory diagnostics, with support gates, unknown-label
counts, conditional denominators and distinct-family counts. The proposal's
controller, automatic generation, admission gates and sampling adjustments are
not activated by loading this file. The current implementation is a source
measurement and annotation foundation, not the complete proposed observer system.

All numerical targets remain launch hypotheses. Full-dataset family/split
conflicts are flagged using family IDs and split names, without reading test
replay/results. An exact normalized replay hash ignores identity and annotations
and flags duplicate structure candidates; it is not a near-duplicate judgment.

Content is counted from actual tokenizer IDs and compiler rows. BOS, EOS and NOOP
are separate from ordinary content. Headers are external content. Open windows
alone do not establish simultaneous generation. External-stream counts exclude
instant insertions. Visible context uses the phase-D decision frontier, so
same-tick generated tokens are not available to other same-tick decisions, old
content disappears at replacement, and closed windows cease providing context.
The compiler currently permits one open/close lifetime per logical window; repeat
tool invocations are counted independently of those lifetimes.

Raw loss weights precede trainer normalization and are not measured gradient
shares. No export concurrency/context quota is evaluated against source counts.
Export manifests, trainer/hardware/exposure logs, historical generation cost,
semantic reaction/dependency/closure annotations and near-duplicate judgments
are explicitly marked unavailable. No response latency or last-needed-evidence
tick is guessed from the last write.

## Storage and compatibility

Opening an existing schema 3/4/5 SQLite project adds a disposable
`trajectory_metrics` table, using the existing supported upgrades for schema 3/4.
The project schema version, tokenizer, source trajectories, revisions and existing
tables are preserved. Older schema-5 readers can ignore the additional cache.
Each cache row belongs to a trajectory UUID with cascading deletion.

The key includes the canonical source hash, tokenizer and control-token settings,
window policy, tool definitions, implementation/metric definitions and launch
configuration. Cached errors also expire on these changes. Reopening with the same
implementation reuses metrics. Source edits/imports/undo/redo and tokenizer
migration automatically hide stale values through hash matching. A background
calculation only publishes its result if the source hash still matches; no project
write lock is held during compilation, and metric reads never pin tool schemas.
Read snapshots are released between samples. Coverage annotation and review-only
edits can reuse numeric execution records; changes to executable source, speaker
names, clock, defaults, tokenizer or tools cannot. Numeric projections and their
sorted index are built once per query snapshot, avoiding repeated JSON decoding
and sorting for every percentile and histogram.
The cache is derived data and can be discarded without losing source information.
