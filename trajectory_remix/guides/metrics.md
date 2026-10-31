# Dataset metrics and coverage gaps

Read `get_dataset_metrics(population="accepted_train_originals")` for the Project
Kidra v0.1 launch review. This population requires the existing human Ready state
(`metadata.validation_level=3`), `split="train"`, and `is_original=true`.
Use `development` for development analysis. Avoid final-test content/results in
observer work. `all` is an explicitly labeled inventory of all source samples,
including unfinished and rejected samples; it is not the accepted denominator.

Call `refresh_dataset_metrics()` to start or join a server-owned background job.
It returns immediately with `job_id`; poll `get_dataset_metrics_status(job_id=...)`
every 1–2 seconds until status is completed, cancelled or failed. Repeated start
calls join a running job, and an MCP timeout/disconnect does not stop it. Omit
limit for all pending sources, or pass `limit=25` to bound one job. Cancellation
uses `cancel_dataset_metrics_refresh(job_id=...)` and finishes the current sample.
Retry resumes remaining sources and reuses saved caches; no source edits, project
revision changes or tool dependency pinning occur. Inspect a failed job's error;
do not keep blindly restarting it. Missing measurements never masquerade as zero.

Use `get_dataset_metric_distribution(metric="peak_generators", edges=[0,1,2,3,4])`
to inspect bins, including empty bins, and summary statistics. `peak_generators`
counts same-tick model content, excluding BOS/EOS/NOOP; it is different from
`peak_open_windows`. `peak_visible_tokens` follows the compiler's pre-decision
phase-D frontier, including replacement and closure. Window lengths, idle runs
and tool latencies have separately labeled pooled distributions.

Use `query_dataset_metrics(population="accepted_train_originals",
metric="peak_generators", minimum=2, limit=20)` to retrieve a numeric slice.
Use `issue="missing_metadata"`, `"unknown_labels"`, `"compile_error"` or
`"pending"` to page issues. Results contain stable UUIDs, source hashes and
dataset revisions. Read source only for the selected samples, using their `id`
with `get_trajectory`.

## Annotating existing examples

Start with `query_dataset_metrics(population="all", issue="missing_metadata")`.
Starting with accepted_train_originals hides legacy sources lacking split/original
assignments. For each selected sample call
`get_metric_annotation_context(sample_id=...)`. This automatically supplies the
metric guide, exact field vocabulary, label definitions, existing annotations,
trace events with evidence paths, current custom dataset/scenario guidance and
saved review contracts. It does not compile or rebuild the numeric cache.

Read every event page using `next_event_offset` until it is null. Excerpts are
explicitly marked and include commands for the complete event or guidance. An
excerpt or one partial page cannot establish absence or correct demonstration.
The context's `expected_revision` guards your eventual metadata merge patch.

Primary domain, terminal outcome and behavioral labels can be reviewed against
the trace. Family grouping, split, original/remix ancestry and historical author,
endpoint/model or prompt version may not be recoverable from the trace. Use
recorded history or an explicit owner-provided policy from the guidance. If those
facts are absent, leave them unknown and report the exact unresolved fields.
Do not invent a provenance value, default all legacy samples to train/original,
or assign random unique family IDs merely to remove alerts. Existing tags are
requested labels, not independently observed evidence. A missing-metadata alert
is a request for information, not proof that the demonstration is invalid.

After coverage-only annotations, numeric rebuilds can reuse compiled measurements
when execution inputs have not changed. Altered agent/user names, clock settings,
events, defaults, tokenizer or tools still require fresh measurements.

Coverage annotations are ordinary top-level `metadata` fields:

```json
{
  "primary_domain": "software",
  "terminal_outcome": "completed",
  "scenario_family_id": "schedule-repair-17",
  "template_family_id": "calendar-conflict-v2",
  "split": "train",
  "is_original": true,
  "provenance": {"author": "author-id", "source": "human-authored", "version": "1"},
  "observed_labels": {
    "capability.delegation": {
      "present": true,
      "required": true,
      "demonstrated_correctly": true,
      "confidence": 0.9,
      "annotator": "independent-reviewer-id",
      "version": "1",
      "evidence": ["replay[0][0]"],
      "justification": "Replace with the specific reviewed evidence and explanation.",
      "review_status": "reviewed"
    }
  }
}
```

This is a schema example, not an annotation to copy onto existing samples.
Condition labels such as `information.false` use `value: true/false/"unknown"`
in place of the three capability booleans. Capability positives require present,
required, and demonstrated_correctly to be true. All booleans must be actual JSON
booleans. All labels need confidence in [0,1], nonempty annotator/version,
justification, evidence paths found in the compiled trace, and reviewed status.
Incomplete labels remain unknown. Existence of an evidence event is a structural
check, not proof the annotation is semantically correct. Review it independently.

For repairs, call `update_trajectory_metadata` with a merge patch and the latest
`expected_revision`. Preserve existing metadata, unknown values and evidence;
never fabricate an annotation to improve a quota. Requested `metadata.tags` are
not observed capability labels. Edits use the normal revision guards and undo.
Edits clear prior evaluations while preserving the trajectory's state under the
existing project policy; a preserved Ready state does not establish annotation correctness.

Launch coverage uses the PDF's bundled provisional domain, capability, condition,
outcome and distinct-family rules. Every fraction carries numerator, denominator
and unknown count. Zero support returns null. Fraction rules below 200 eligible
sources report insufficient support; family minima remain pilot diagnostics below
50,000. Conditional rules use their own eligible denominators. No target changes,
generation dispatch, admission decisions or sampling changes are performed.

Raw compiler weights describe supervision allocation before trainer normalization;
they are not gradient fractions. Source concurrency is not exported remix exposure.
Export/context quotas, chunk overhead, actual trainer cost/exposure, generation
cost, reaction/dependency/closure judgments and near-duplicate judgments are
explicitly unavailable without their required logs or reviewed annotations.
Tool failures and high NOOP fractions are diagnostics, not automatic rejections.
