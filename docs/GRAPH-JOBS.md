# Jobs, recovery and bulk operations

Everything that takes a while in Remix, such as generation, review, import, export, bulk edits and
metric calculation, runs as a **job**. Jobs are durable: they survive closing the browser, they
can be paused, and they come back safely after a restart. This page explains the rules they follow.

The job layer builds on the [source service](GRAPH-SOURCE.md) and the
[artifact service](EXPORT.md), and [generation](GRAPH-GENERATION.md) plugs Draft, Assembly and review
into it. Jobs never execute synthetic tools.

## What you need to know

| Concept | In practice |
| --- | --- |
| **Ownership** | A pipeline working on a trajectory *owns* it exclusively. Edits from anywhere else are refused until it finishes or is detached. There is no force-unlock. |
| **Attached cap** | The number of trajectories owned at once (including paused pipelines) is capped. |
| **Frozen inputs** | A run freezes its settings, instructions, sampler proposals and checklists when it is created, with digests. Later edits never change an existing run. |
| **Per-endpoint queue** | Each logical endpoint has one FIFO queue with its own concurrency and retry limits, even if two endpoints share a URL. |
| **Pause / Resume / Cancel** | Per job. Pause keeps ownership and progress, and Cancel is final. |
| **Detach** | Permanently releases one trajectory. No quality verdict is invented. |
| **Restart** | Unfinished jobs come back **paused**. Unaccepted response text is discarded, and nothing restarts by itself. |

### Pause, cancel and detach

- **Pause** stops callbacks from publishing and keeps ownership, so you can resume later.
- **Cancel** blocks all results, cancels pending items, asks active provider calls to stop, keeps
  committed edits, detaches started attempts and marks unstarted attempts cancelled. It never changes
  quality stages or fabricates failures. A provider may still finish externally, but its response
  can't be published.
- **Detach** permanently releases a trajectory and fences its pending requests. In a manual bulk
  review it marks the item `detached`, and edits already committed stay in Undo history. A job
  finishes when nothing is pending or working, even if it was paused when the last detach happened.
- **Resume** is rejected for finished jobs.
- **Phase barrier:** with `generation.phase_barrier` on, a run parks each trajectory before its
  reviews (parked trajectories don't count toward the active-trajectory cap) and pauses once all
  are drafted and assembled. See
  [Generate and review in separate sessions](GRAPH-GENERATION.md#generate-and-review-in-separate-sessions).
- **Held trajectories:** a trajectory that needs manual intervention (output that stays invalid,
  a context limit, a local error) is held, and the rest of the job keeps running. The job pauses
  only when nothing but held trajectories is left. Resume retries them; detach settles one.

### Endpoints that run out of retries

When transport retries are exhausted, **only that endpoint pauses**. Jobs keep their sources,
owners, stages and checkpoints, and other endpoints carry on. Use **Resume endpoint** once your server
is back. This also resets queued retry budgets. There is no probe, fallback model or priority
ordering, and endpoint Resume doesn't resume a job you paused separately.

### Attempts and outcomes

A run of N attempts reserves N trajectory identities up front. Each ends in exactly one detached outcome
(pass, fail or abandoned), and retries never create replacements. Only an explicit outcome ends a
pipeline, and quality stages are the driver's explicit edits, not something the queue infers.

## Bulk operations

A **bulk job** applies one action to every trajectory matching a search, across all pages. It snapshots
the matching IDs and revisions at the start, so trajectories created later aren't included.

The selector is `stage`, `split`, `text` (case-insensitive substring of the serialized source) and
`tool` (an indexed tool ID), combined with AND. The studio's list uses the same selector.

| Action | Config | Notes |
| --- | --- | --- |
| `stage` | `{"value": -3..3}` | |
| `split` | `{"value": "Train" / "Development" / "Test" / "Auto"}` | |
| `metrics` | | Fills only absent or null metrics. |
| `delete` | | Keeps historical revisions and a deletion guard. |
| `review_repair` | | Uses the queued async driver ([generation](GRAPH-GENERATION.md#manual-review)). |

Anything edited between the snapshot and its turn is a conflict, and owned entries are skipped, each
with a warning. A paused operation keeps its locks and targets and needs manual Resume. A failing
handler pauses the job unless it's an explicit skip. Cancelling stops in-flight calculations, and
completed changes remain available.

### Undo and Redo

After a job settles, its history holds **only the changes that really happened**. The browser
combines them into **one** Undo action. Each change records its before and after, expected revision
and expected commit. Undo restores a higher revision, and Redo re-applies saved results. Neither
calls a handler or a model. Revision, commit and ownership are checked per entry. Entries that were
edited in between produce warnings, and only successfully applied changes feed the inverse action.

## Logs and artifacts

- Each job has its own text log with monotonic cursors. The project-wide cap is **Log cache size**
  (1024 MiB by default), and the oldest text is evicted first. A cursor reports `truncated` when
  what you asked for is gone. Clearing or eviction **never** touches sources, sign-offs, owners,
  checkpoints or bulk history. Drivers must redact secrets.
- **Export logs** creates a separate download job from a snapshot of the retained text, so
  further appends or clears can't change it.
- Export jobs pin immutable sources and settings in one transaction, build outside write
  transactions, and check cancellation before publishing, removing unaccepted bundles. Downloads use
  leases, publish only when complete, and expire after 24 hours. Finished job records outlive the files
  (an expired file gives 404). See [Export](EXPORT.md).

## REST routes

All under `/api/v1` and authenticated like the rest of the studio. The headless factory
`create_job_app` is for tests and isn't an authenticated public deployment.

| Route | Result |
| --- | --- |
| `GET /jobs?limit=&offset=`, `GET /jobs/{id}?include_config=false` | Independent snapshots, progress and paused endpoints |
| `POST /runs` `{config, proposals}` | 202 with a job ID |
| `GET /jobs/{id}/attempts` | Frozen attempts and outcomes |
| `POST /jobs/{id}/pause`, `/resume`, `/cancel` | Per-job control |
| `POST /jobs/{id}/detach/{trajectory}` | Permanent detach |
| `GET /jobs/{id}/phase`, `POST /jobs/{id}/phase/review` | A run's [phase barrier](GRAPH-GENERATION.md#generate-and-review-in-separate-sessions): read it, or start the reviews early |
| `POST /endpoints/{id}/resume` | Explicit endpoint recovery |
| `POST /bulk` `{query, action, config}` | 202 with a frozen-target job |
| `GET /jobs/{id}/results`, `/history` | Paged results and settled changes |
| `POST /bulk/history` `{deltas, undo}` | Apply Undo or Redo, with conflict warnings |
| `GET /jobs/{id}/logs?after=&limit=` | Cursor page, last cursor, truncation flag |
| `GET /logs/usage`, `DELETE /logs?job=` | Retained bytes and cap, and clearing |
| `POST /logs/{id}/export` | 202 with a snapshot download job |
| `POST /exports` `{options, profile, active_limit, seed}` | 202 with a pinned export job |
| `GET /jobs/{id}/artifacts/{filename}` | A leased download, or 404 |
| `GET /changes?after=&limit=` | Bounded invalidations, the latest cursor, and a `resync` flag |

The change feed keeps the latest 1,000 invalidations. When `resync=true`, refresh lists and the
selected record instead of replaying. Conflicts return **409**, invalid input **422**, and missing
records or expired artifacts **404**.

A `JobWorker` owns the tasks, so closing a dialog or dropping an HTTP connection never cancels work.
Run drivers that aren't installed pause with an explanation, and shutdown pauses workers and waits.

## For developers

This section covers the Python service boundary.

```python
from trajectory_remix.graph import GraphStore
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.bulk import BulkService

jobs = JobService(GraphStore(project_path), active_limit=20)
bulk = BulkService(jobs)
```

- Use **one `JobService` per project worker process**. Construction recovers unfinished jobs to
  paused, bumps worker epochs and discards unaccepted bodies. Starting another recovering service
  fences the previous one, and extra read-only connections can use `recover=False`.
- Job tables are `graph_*` and separate from source metadata. `GraphStore.replace` and
  `GraphCommands` refuse edits to an owned trajectory (`OwnershipConflict`) even with the latest
  revision.
- `create_run(config, proposals)` freezes everything and reserves one attempt and UUID per proposal.
  `start_attempt` creates the source and its ownership atomically, and `acquire(job, trajectory,
  expected_revision)` takes the same slot for manual work.

```python
request_id = jobs.enqueue(job, trajectory_id, endpoint_id, step_id, payload)
ticket = await jobs.execute_one(endpoint_id, provider_call)
if ticket is not None:
    body = jobs.result(ticket)
    jobs.accept(
        job,
        ticket.epoch,
        trajectory_id,
        ticket.step,
        updated_source,
        expected_revision,
        checkpoint,
        request=ticket,
        outcome=None,
    )
```

- `provider_call(ticket)` is **one** complete API invocation with no hidden retries, returning
  complete JSON. Stream into a throwaway buffer only. Don't wrap several model or tool turns in it,
  because every further API call needs its own `enqueue`. Enqueue is idempotent by `(job, step)`, and
  reusing a step with different input is rejected.
- A failed call retries the same FIFO item with a fresh copy of its payload. Tickets carry an epoch
  and a claim lease, so stale tickets can't publish after a Resume, pause, restart, cancel or detach.
  Only one item per trajectory may be running across all endpoints.
- `accept` atomically commits the source revision, checkpoint, idempotency record, request consumption,
  optional detachment and change-feed entry. A source CAS conflict rolls everything back. Call
  `checkpoint(job, trajectory)` when resuming.
- Pass `outcome='pass'`, `'fail'` or `'abandoned'` only when a pipeline ends. Provider execution and
  billing aren't promised to be exactly-once across ambiguous failures.
- Bulk: `bulk.start(query, action, config)`, then `advance`, `results`, `history` and `restore`.
  Metrics and review handlers are synchronous hooks (`handler(SourceRecord, frozen_options)`) for
  deterministic work, not a live review pipeline. Missing handlers are rejected before an HTTP job
  is created.
- `JobArtifacts(jobs, ArtifactService(...))` wires exports: `start_export`, `build_export`,
  `export_logs` and `lease`.

```bash
python -m pytest -q tests/test_graph_jobs.py tests/test_graph_foundation.py \
  tests/test_graph_compiler.py tests/test_graph_export.py
```

The fixtures use two endpoints, at most three sources and fake clocks and drivers. They cover FIFO
and retries, caps, frozen proposals, ownership, idempotent acceptance, fencing, bulk Undo/Redo, log
eviction and real raw export jobs. There are no benchmarks, scale tests, training or live model calls.
