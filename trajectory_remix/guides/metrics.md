# Dataset metrics and coverage gaps

Read `get_dataset_metrics(population="accepted_train_originals")` for the Project
Kidra v0.1 launch review. This population requires the existing human Ready state
(`metadata.validation_level=3`), `split="train"`, and `is_original=true`.
Use `development` for development analysis. Avoid final-test content/results in
observer work. `all` is an explicitly labeled inventory of all source samples,
including unfinished and rejected samples; it is not the accepted denominator.

Call `refresh_dataset_metrics(limit=25)` until `processed=0` to fill missing
compiler caches. It performs no generation, source edits, state changes, or tool
dependency pinning. Each call handles at most 100 sources, one at a time in a
worker. Stop between calls. Query results show pending calculations and errors;
missing measurements never masquerade as zero.

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
An edit can invalidate prior review/Ready state under the existing project policy.

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
