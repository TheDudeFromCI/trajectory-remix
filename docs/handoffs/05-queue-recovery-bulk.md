# 05 — Durable jobs, FIFO provider requests, ownership, recovery and bulk

Read [CONTEXT](CONTEXT.md), contracts **§5–7 and §9–10**. Requires 01; coordinate
artifact services with 02. This backend should precede 03's completed job UI and
06's parallel generation, even though its number is 05. Suggested branch:
`feat/job-queue-recovery-bulk`.

## Inspect first

`model_session.py`, `generation_state.py`, `generation_resume.py`,
`generation_recovery.py`, `generation_log.py`, `metric_jobs.py`, `storage.py`,
`trajectory_review.py` and `server.py`. Reuse transport/SQLite knowledge, not old
checkpoint formats, single-current-job globals or executable tool sessions.

## Scope

- Durable run/attempt/job/request/checkpoint records and resource ownership.
  Multiple independent jobs, separate logs; no transaction held during network
  work or expensive compute. Current project source is not the job log store.
- One independent FIFO queue per logical endpoint, configured concurrency caps.
  Every separate model/tool-turn API call is an item; transport retries stay in
  that item until success/exhaustion. No manual or cache-affinity priority.
- Global active-trajectory limit plus endpoint caps. Generation is across
  trajectories; at most one generation request per trajectory. One shared
  review/repair operation slot per trajectory across transports.
- Freeze run instructions/config/proposals. Atomic event/checkpoint/owner/change
  commits; idempotent acceptance and fenced late responses. No false guarantee
  of exactly-once paid provider execution.
- Attached pipelines remain locked through all work. Completed/abandoned/cancelled
  attempts detach. Detachment permanent; manual review/repair only on detached
  sources. Reject/skip conflicts rather than overwriting/detaching automatically.
- Pause retains resumable ownership. Cancel terminal, stops pending/active work
  where possible, retains commits, detaches started attempts after settling.
  Cancel is not a quality verdict. N attempts counts N detached outcomes, not
  retries/API calls/passes; don't generate replacements for failures.
- Discard incomplete response text entirely, not diagnostic drafts. Keep completed
  events/repair patches. Endpoint retry exhaustion pauses it without stage
  demotion, affects only its queue, and requires manual Resume. No probes/fallback.
  Server restart also waits for manual Resume, even with healthy endpoints.
- Job APIs: inspect/paginate/status/progress/log cursor, pause/resume/cancel/detach
  where eligible, bounded change feed. Dialog closure does not stop server work.
- Log cache optional cap default **1024 MiB**, oldest-first eviction, clear/export
  controls. Clearing text never clears checkpoints/source/owners/signoffs/Undo.
- Search-wide bulk snapshots IDs/revisions across all pages, bounded commits,
  warnings for locked/skipped/conflicting entries. Actions: stage, split,
  calculate missing metrics, delete, Review & Repair via handler interfaces.
  One Undo result of actual changed-entry deltas after settlement; Redo never
  calls a model. Browser owns history; backend enforces locks/CAS.

03 consumes these generic job interfaces. 06 plugs in the real Flow/review/repair
drivers; backend tests here use deterministic fake drivers, not local/hosted LLMs.
Some primitives can be split into an earlier focused PR if needed, but do not
enable real parallel generation before ownership/queue correctness exists.

## Small checks

Use 2–3 mock trajectories/endpoints: FIFO order, multiple calls from one source,
retry stays current item, unaffected endpoint continues, caps/exclusive slots,
frozen config, no duplicate publication, interrupted-body discard, no late writes
after detach/cancel, manual-only restart, attempt count and partial bulk results.
Fake clocks/file budgets test cache/expiry without waits/large log generation.
No 20-active/25k load run, elapsed-throughput assertions or live provider calls.

Deliver a focused draft PR, route/driver contract docs and mock correctness
evidence. Record precise interfaces for 03/06; don't merge.
