# Graph jobs, recovery and bulk operations

The [manual graph studio](GRAPH-STUDIO.md) supplies authenticated REST, manual
authoring, read-only previews, import/export jobs and focused agent tasks.

The graph runtime extends the [source/CAS service](GRAPH-SOURCE.md) and uses the
[temporary artifact service](GRAPH-COMPILER.md). It never executes synthetic tools.
The [event Flow](GRAPH-GENERATION.md) registers queued generation, review and
continuing repair drivers.

## Service and process boundary

```python
from trajectory_remix.graph import GraphStore
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.bulk import BulkService

jobs = JobService(GraphStore(project_path), active_limit=20)
bulk = BulkService(jobs)
```

Construct one `JobService` per graph project worker process. Startup adds only
`graph_*` runtime tables to the new graph project, without reading or migrating
old projects. Construction recovers unfinished jobs to **paused**, increments
worker epochs and discards all unaccepted response bodies. Nothing restarts
provider work automatically. The host must coordinate a single recovering process;
starting another recovering service intentionally fences the previous process.
Additional cooperating connections can use `recover=False` for queries/tests.

Source revisions/settings keep their existing immutable identities. Jobs, runs,
attempts, owners, endpoint queues, requests, steps, per-source checkpoints, logs,
bulk results and deletion guards occupy separate tables. Runtime state is not
source metadata or compiler input. Graph edits through `GraphStore.replace` or
`GraphCommands` reject active ownership with `OwnershipConflict`, even if the
caller supplies the latest revision. Private `_commit` is reserved for services
that fence and commit ownership in the same transaction.

`create_run(config, proposals)` freezes JSON config, settings revision/commit,
and all proposals, with content digests. Supply already resolved instructions,
checklist/matrix references, seeds and targets in this snapshot; credentials
belong only in the injected provider transport. One proposal reserves one attempt
ordinal and a distinct trajectory UUID. Later project edits never resample an
existing run. `attempts(job, limit=..., offset=...)` returns these identities.

`start_attempt(job, ordinal, source)` requires that reserved UUID and creates the
source plus attached ownership atomically. The global cap counts attached
trajectories, including paused pipelines. An existing or previously detached
source cannot be attached to another attempt. User-created sources are detached;
manual review/repair uses `acquire(job, trajectory, expected_revision)` for the
same exclusive slot across transports. A generation pipeline retains ownership
through all its reviews/repairs. There is no force-unlock or overwrite option.

## Request and driver contract

One logical endpoint ID owns one FIFO queue, even if two endpoints share a URL.
Concurrency/retry budgets use the actual `ProjectSettings.endpoints` fields
`concurrency` and `retries` (retries means additional transport tries). A run's
request retry budget stays pinned to its frozen settings. Endpoint caps are
shared operational limits; configuration can be refreshed explicitly with
`configure_endpoints()`.

```python
request_id = jobs.enqueue(job, trajectory_id, endpoint_id, step_id, payload)
ticket = await jobs.execute_one(endpoint_id, provider_call)
if ticket is not None:
    body = jobs.result(ticket)
    # Validate complete body, build one event/review/repair patch outside SQLite.
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

`provider_call(ticket)` is an async callback for **one** complete API invocation.
Its transport must disable hidden retries, and return only complete JSON data.
Keep streaming text in a disposable local buffer; never append it to logs,
checkpoints or source. Exceptions retry the same FIFO item with a fresh deep
copy of its original payload, while retaining its endpoint and trajectory slot.
Do not wrap multiple model/tool turns inside this callback. Each subsequent
API call gets another `enqueue` step. Enqueue is idempotent by `(job, step)`;
reusing that key with different input is rejected.

`claim(endpoint)`, `request_result(ticket, body)` and `retry(ticket)` expose the
same boundary for adapters that manage their own dispatch. Tickets carry both a
job epoch and a unique claim lease; stale tickets cannot publish after an
endpoint Resume, job pause, restart, cancellation or detachment. Only one item
per trajectory may be running/awaiting acceptance across all endpoints. Claims
never overtake an earlier runnable item blocked on that trajectory. Paused jobs
are ineligible and do not stop unrelated jobs on the endpoint. A ready body
continues to occupy the source slot until accepted.

Transport exhaustion pauses only that endpoint; jobs retain their sources,
owners, stages and checkpoints. `inspect(job).paused_endpoints` exposes the
condition. Explicit `resume_endpoint(id)` resets queued retry budgets. There is
no probe, fallback model, priority or cache-affinity ordering. Other endpoints
continue. Manual endpoint Resume does not Resume a separately paused job.

`accept` atomically commits the source revision, per-source checkpoint,
idempotency record, request consumption, optional detachment and change-feed
notification. Repeated identical acceptance returns the original commit; a
changed payload under the same step key is rejected. Source CAS conflicts roll
back the entire acceptance. Call `checkpoint(job, trajectory)` when resuming;
the job's `checkpoint` field is only its most recently accepted checkpoint.

Pass `outcome='pass'`, `'fail'` or `'abandoned'` only when the pipeline ends.
Quality stage/signoff changes are the driver's explicit source edits, not
inferences made by the queue. Detachment increments the run's finished-attempt
count, regardless of pass/fail; all N detached outcomes complete an N-attempt
run. Requests/retries never create replacement attempts. Manual jobs can call
`finish(job, epoch)` after releasing all ownership.

`control(job, 'pause'|'resume'|'cancel')` controls an individual job. Pause fences
active callbacks and retains resumable ownership. Cancel fences all results,
cancels pending items, requests cancellation of active async provider calls,
retains committed edits, detaches started attempts and marks unstarted attempts
cancelled. It does not change quality stages or invent failed attempts. SQLite
serialization settles authorized source writers before locks are released;
a provider may still finish externally, but its fenced response cannot publish.
`detach(job, trajectory)` permanently releases that trajectory and fences its
pending requests. For manual review/repair bulk jobs it also settles the item as
`detached`, increments progress once, and retains changed-only Undo history for
already committed edits. The job completes when no pending/working items remain,
including detachment while paused. Detachment does not invent a quality verdict.
Resume is rejected for terminal jobs. Idempotent acceptance
never promises exactly-once provider execution/billing across ambiguous failures.

## Frozen bulk targets and history

```python
job = bulk.start({"stage": 0, "split": "Auto"}, "stage", {"value": 1})
bulk.advance(job, batch_size=25)  # repeat in a host-owned task until settled
results = bulk.results(job, limit=100, offset=0)
deltas = bulk.history(job, limit=100, offset=0)
undo = bulk.restore(deltas, undo=True)
redo = bulk.restore(undo["deltas"], undo=False)
```

`start` uses one `INSERT ... SELECT` to snapshot every matching ID/revision across
all pages. Graph query fields are `stage`, `split`, `text` (case-insensitive
substring of serialized source) and `tool` (indexed tool ID); supplied fields
combine with AND. This is an explicit new graph selector, not the old replay
search grammar. The studio must use this same selector for matching listings.
Subsequently created matches are excluded. Edits between snapshot and processing
are conflicts; owned entries are skipped with per-entry warnings. Batch size
bounds commits; calculations happen outside write transactions. Concurrent
workers cannot process the same bulk entry twice.

Actions are `stage`, `split`, `metrics`, `delete`, `review_repair`. Stage config is
`{"value": -3..3}`; split is `{"value":"Train"|"Development"|"Test"|"Auto"}`.
The latter two driver actions require registered handlers:
`handler(SourceRecord, frozen_options)`. Metrics returns a JSON object of values;
only absent/null keys are filled. Review/repair returns a complete graph source,
with the same UUID. These synchronous hooks support deterministic calculations
and already completed review results; they do not enable a live review pipeline.
The orchestration layer supplies queued async review/repair jobs using the
request/acceptance contract above, rather than making hidden provider calls from
a bulk callback. Missing handlers are rejected before HTTP job creation.

Bulk owners are the same exclusive manual-operation owners. Paused operations
retain their locks and frozen targets; restart requires manual Resume. Handler
failures pause resumably unless they are explicit validation/conflict skips.
Cancellation fences in-flight calculations; completed deltas remain available.
No uncommitted calculation can append a late history entry.

After completion/cancellation, `history` returns **only actual changes**. Fetch
all history pages into one browser Undo action; do not create one action per
batch. Each delta has `id`, `before`, `after`, `expected_revision` and
`expected_commit`. Delete removes the current pointer and tool index while
retaining immutable historical revisions plus a deletion guard. Undo restores
with a higher revision; Redo deletes/edits saved results without invoking any
handler or model. Both revision and commit identity plus ownership are checked
per entry. Return warnings for intervening edits and retain only successfully
applied deltas for the inverse browser action. Browser history remains local.

## Logs and temporary artifacts

`append_log(job, text)`, `logs(job, after=cursor, limit=...)`, `clear_logs(job=None)`
and `log_usage()` expose independent per-job text with project-wide oldest-first
byte eviction. The project cap is `log_cache_mib` (1024 by default, `None` uncapped).
Eviction runs on append; tests may inject a tiny byte budget. Monotone cursors
survive clearing and report `truncated` when requested history was lost. Clearing
or eviction never touches sources, signoffs, owners, checkpoints or bulk history.
Jobs can append new entries after clearing. Drivers must redact transport secrets.

`JobArtifacts(jobs, ArtifactService(...))` wires the existing leased `/tmp`
service. `start_export(options, profile=...)` pins immutable source references
and settings in one transaction; `build_export(job)` runs the real raw/compiled
exporter outside write transactions. A cancellation callback and final epoch
check prevent late artifact publication. Unaccepted finished bundles are removed.
`export_logs(source_job)` creates a separate completed download job from a WAL
snapshot of retained text/cursor, streamed without building one large string.
Clear/append cannot change that snapshot. Manifest metadata reports truncation.

Use `lease(job, filename)` for downloads. Writer/download locks, complete-only
publication, partial cleanup and 24-hour expiry use the shared artifact contract.
Completed compact job records survive file expiry/OS cleanup; missing artifacts
return 404. Startup and the host's periodic sweep call `ArtifactService.expire()`.

## REST integration for the graph studio

`job_router(jobs, bulk, artifacts, worker)` mounts the shared application layer at
`/api/v1`. `create_job_app(path, handlers=..., bulk_handlers=...)` is a headless
factory, independent of the replay dashboard. The manual studio mounts this router
inside its authenticated local transport and preserves its Host/origin policy.
Do not expose the headless factory as an authenticated public deployment.

| Route | Result |
|---|---|
| `GET /jobs?limit=&offset=`; `GET /jobs/{id}?include_config=false` | Independent snapshots/progress/paused endpoints |
| `POST /runs` `{config,proposals}` | 202, job ID and status URL |
| `GET /jobs/{id}/attempts?limit=&offset=` | Frozen attempts/outcomes |
| `POST /jobs/{id}/pause`, `/resume`, `/cancel` | Per-job control |
| `POST /jobs/{id}/detach/{trajectory}` | Guarded permanent detach |
| `POST /endpoints/{id}/resume` | Explicit endpoint recovery |
| `POST /bulk` `{query,action,config}` | 202, frozen target job |
| `GET /jobs/{id}/results`, `/history` | Paginated results/settled deltas |
| `POST /bulk/history` `{deltas,undo}` | Changed inverse deltas and conflict warnings |
| `GET /jobs/{id}/logs?after=&limit=` | Cursor page, last cursor, truncation |
| `GET /logs/usage`; `DELETE /logs?job=` | Retained bytes/cap; clear text |
| `POST /logs/{id}/export` | 202, retained-snapshot download job |
| `POST /exports` `{options,profile,active_limit,seed}` | 202, pinned real export job |
| `GET /jobs/{id}/artifacts/{filename}` | Leased completed download, or 404 |
| `GET /changes?after=&limit=` | Bounded invalidations, latest cursor, resync flag |

The feed shares the foundation's latest-1000 invalidations. On `resync=true`,
refresh lists and selected records instead of replaying an incomplete history.
409 denotes revision/ownership/stale-worker conflicts; 422 invalid inputs; 404
missing records/artifacts. Config/source diagnostic validation remains separate.

`JobWorker` owns tasks, so closing a dialog or disconnecting HTTP does not cancel
work. It dispatches bounded bulk chunks and real exports. Inject async
`handlers[kind](jobs, job_id)` for orchestration; each must resume from durable
attempts/checkpoints and use the queue rather than hidden provider concurrency.
Uninstalled run drivers pause with a short explanation. They do not silently
complete a production placeholder. Shutdown pauses workers and waits for them.

## Small deterministic checks

```bash
python -m pytest -q tests/test_graph_jobs.py tests/test_graph_foundation.py \
  tests/test_graph_compiler.py tests/test_graph_export.py
```

Fixtures use two endpoints, at most three sources and fake clocks/budgets/drivers.
They cover FIFO/retry slots, independent caps, frozen proposals, N outcomes,
manual exclusivity, atomic/idempotent acceptance, per-source checkpoints,
concurrent claims/bulk workers, pause/cancel/restart fencing, ignored provider
cancellation, endpoint claim leases, search-wide snapshots, partial results,
changed-only Undo/Redo, log eviction/clear/export, real raw artifact jobs and
HTTP controls/downloads. No benchmarks, scale tests, full training or live model
calls are part of this validation. Event Flow/review/repair and metric population
views use these same services.

Generation and manual Review & Repair now use the shared [event Flow](GRAPH-GENERATION.md).
See [local-LLM validation](GRAPH-LOCAL-LLM.md) for user-machine quality checks.
