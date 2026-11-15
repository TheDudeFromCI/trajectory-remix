# Export and jobs

`export_graphs` returns a job reference immediately. Options include `compiled`
(default false in the agent task), `minimum_stage` (Ready/3 by default),
`include_stale` (true), `trajectories_per_file` (256), `exclude_missing_split`
(false), and `metadata`: `full`, `remove_all`, `measured_metrics`, `all_metrics`
(default). Compiled export requires an existing compatible `profile`; it is
independent of the browser's preview preference. Raw export needs no tokenizer.

Use `inspect_graph_job` to follow that ID; other jobs are independent. REST
provides per-job pause/resume/cancel and cursor logs. Cancellation is terminal,
retains committed work, and fences late publication. Restart requires manual
Resume. Model work uses configured roles through the shared event Flow.

Use `graph_job_items`, `graph_job_logs`, and explicit `control_graph_job` for
bounded inspection and lifecycle controls. `graph_job_artifacts` lists paths.
Once completed, read `/api/v1/jobs/{id}/manifest` and download listed files,
`manifest.json`, or `download.zip` from `/api/v1/jobs/{id}/artifacts/{filename}`
with the local session token. Files expire after 24 hours and may disappear
sooner through OS cleanup. Sources/settings are pinned when the job starts.
Missing split warns and auto-assigns; explicit `Auto` is intentional. Known
families stay together; conflicting explicit family splits block export.

Browser Undo is local and uses settled changed-entry deltas with source guards.
Redo restores saved results, never reruns models or calculations.
