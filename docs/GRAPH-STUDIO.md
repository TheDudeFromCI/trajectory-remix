# Manual graph studio

The graph studio uses the [source](GRAPH-SOURCE.md), [compiler](GRAPH-COMPILER.md)
and [durable jobs](GRAPH-JOBS.md) services. It retains the dataset sidebar,
window cards, scrub timeline, event forms and advanced Source JSON editor.
There is no sandbox, authored tick marker or executable tool runtime in this UI.

[Graph view](images/graph-studio.png) · [Tick history](images/graph-studio-ticks.png)
show the same tiny source inspected in Chromium.
The replay dashboard remains available for old projects; it does not migrate them.

## Start without a tokenizer

```bash
pip install -e '.[export,mcp,test]'
trajectory-remix graph-init --dataset graph.sqlite
trajectory-remix graph-studio --dataset graph.sqlite --port 8765
```

Open `http://127.0.0.1:8765`. New projects need no tokenizer. Create a trajectory,
add windows/events and Save trajectory. Drafts can contain structural errors;
diagnostics identify stable event IDs and source paths. Delete preserves broken
references, whose dependency chips remove them explicitly. Same-window writers
must be ancestor ordered. Edits use per-source revision guards and operational
locks. A conflict leaves browser text intact; Reload saved source is explicit.

Metadata is separate from intrinsic event/window data. Missing split warns;
explicit Auto is intentional. The sidebar's JSON editor preserves arbitrary
nested annotations. Stage/split changes do not alter intrinsic source identity.
Dataset Metrics is a single panel showing saved values/unavailable state; graph
metric calculation and population views await the metric service integration.
Generation, automatic review and repair drivers are not installed in this
manual milestone. The UI does not expose the old generation path for graph data.

## Forms and timeline

Select an event to edit its stable window/dependencies and whole-event mask/loss.
Add a dependency with the searchable picker, or Shift-click a predecessor in the
timeline. Hover shows its dependencies. Delete a chip to remove that edge.
Tool request/response begin and end, Close window, replacement, delivery and
instant chaining are structural controls. Chat uses a speaker field and derives
atomic MSG framing. Bordered uppercase controls are visual labels, never text
inserted into authored content. Tool content may be intentionally partial JSON.
Form mode works with a complete object and preserves unknown argument fields;
unparseable content stays intact through raw/form mode changes.

Graph mode is the default and does not construct a tokenizer or scheduler.
Scrubbing selects a prefix of a stable dependency order, **not authored time**.
Tick history is opt-in, using the actual compiler scheduler. Its tokenizer ID
and trailing debounce (default **500 ms**, configurable 0–10000 ms) are browser
Preferences. Definitions and dedicated control maps belong to Project Settings;
incompatible profiles are rejected without extending vocabulary. Export's
selector is independent of the preview preference.

Tick previews include unsaved event drafts, write no source/metrics/artifacts,
and show at most 1000 frames in the browser (the API permits 1–5000). A truncation
label identifies the bound; this milestone has no unbounded range-preview API.
Edits reset the timer, clear obsolete frames, and fence stale responses by draft,
selection, mode, profile and settings. A request key identifies the exact client
draft; `source_hash` in the result is the server's normalized intrinsic hash.
Server cache keys also cover compiler version, profile fingerprint, settings
commit, capacity, seed and frame bound. Server cache limits are 16 entries / 8
MiB; browser preview reuse retains up to four responses. Source commits and
export settings never depend on preview state.

## Jobs, import, export and history

Import accepts graph JSON, arrays, or JSONL, bounded to 40 MB per request.
Imports are durable independent jobs with per-item atomic source/cursor commits.
Invalid items or existing UUIDs are skipped with log warnings; existing sources
are never overwritten. Pause/restart waits for manual Resume; cancellation keeps
committed items. Split larger uploads explicitly. Parsed upload data remains in
the job's frozen config for restart recovery, separate from evictable logs.

The bottom bar opens independent job dialogs. Polling uses compact job summaries
and paginated source projections, without loading the dataset into the browser.
Dialogs have separate log cursors, pause/resume/cancel and endpoint-resume
controls. Opening or polling jobs preserves the selected event and draft.
Logs page in groups of 100; retained browser log text is bounded to 200k
characters. Clear/export uses server-retained logs. Settings exposes the optional
project-wide MiB cap, default 1024, and Clear Log Cache.

Export starts a real pinned raw/compiled job. Ready is the default minimum;
stale inclusion defaults on. Choose shard size, split exclusion and metadata
projection independently of preview. Missing split warnings and family conflicts
come from the shared exporter. Completed dialogs list manifest, split shards,
profile assets and ZIP downloads through authenticated leased routes. Files
live in job-owned `/tmp` directories and expire after 24 hours (or earlier OS
cleanup). Partial or cancelled files cannot be downloaded.

Search filters text, stage, split and tool using the **same** selector as bulk
jobs. Bulk applies to all frozen matches, across pages. It reports lock/revision
skips. Once settled, one local Undo action contains actual changed-entry deltas;
Redo restores saved results. Individual saved edits and deletions also use
revision/commit-guarded deltas. History stays in this browser session, capped at
50 actions. Unsaved drafts must be saved or reloaded before restoring history.
The status bar shows the newest 50 jobs; API pagination accesses older jobs.

## REST and MCP

All dashboard project/job operations use REST. `/api/*` requires the same-origin
`X-Remix-Token` injected into HTML; localhost Host checks and a restrictive CSP
match the existing local transport. Tokens are never put in download URLs.
`/mcp` uses the existing SDK's Host/origin/session policy and optional origin
allowlist. Install the MCP extra for that transport; REST remains usable without
it. The existing `trajectory-remix-mcp --url http://127.0.0.1:8765` stdio bridge
uses the same authenticated agent handlers.

| Route | Behavior |
|---|---|
| `GET /api/v1/project`, `/settings` | Project identity/capabilities; versioned settings |
| `PUT /api/v1/settings` | `{expected_revision, settings}`; refresh endpoint configuration |
| `GET /api/v1/schema/{source,settings}` | Actual Pydantic contract JSON Schema |
| `GET /api/v1/trajectories` | `text`, `stage`, `split`, `tool`, `limit`, `offset`; compact summaries and total |
| `POST /api/v1/diagnostics` | Validate/normalize a draft DTO and diagnose current settings without saving or tokenizing |
| `POST /api/v1/trajectories` | Complete graph DTO; 201 with revision/diagnostics |
| `GET /api/v1/trajectories/{id}?revision=` | One current/historical record plus current lock owner |
| `PUT`, `PATCH`, `DELETE /api/v1/trajectories/{id}` | Revision-guarded replace, RFC 7396 patch, or deletion delta |
| `POST`, `PATCH`, `DELETE /api/v1/trajectories/{id}/events/{event}` | Stable-ID event commands; each requires expected revision |
| `PUT /api/v1/trajectories/{id}/events/{event}/dependencies` | `{expected_revision, after}` replaces the dependency list |
| `GET /api/v1/trajectories/{id}/graph`, `/diagnostics` | Tokenizer-free order/diagnostics |
| `POST /api/v1/trajectories/{id}/preview` | `{expected_revision, profile, source?, client_key?, active_limit?, seed?, max_frames?}`; read-only draft Tick history |
| `POST /api/v1/imports` | `{text}`; 202 job/status reference |
| `GET /api/v1/job-summaries?limit=&offset=` | Compact jobs without frozen source/config payloads |
| `GET /api/v1/jobs/{id}/manifest` | Completed artifact manifest or 404 after expiry |
| `GET /api/v1/agent/guide/{topic}` | One focused public guide |

Job, bulk, change-feed, export and download routes are the existing
[shared job router](GRAPH-JOBS.md#rest-integration-for-the-graph-studio).
Invalid JSON/DTOs return 422 with field locations; structurally invalid graphs
return editable diagnostics; revision/ownership conflicts return 409; missing
records/artifacts return 404. No model/provider access is added.

MCP offers `get_graph_session`, `get_authoring_guide`, `list_graphs`, `read_graph`,
`create_graph`, `patch_graph`, `patch_graph_event`, `export_graphs`, and
`inspect_graph_job`. These use Studio/GraphCommands, not parallel edit logic.
Read focused guides `start`, `dependencies`, `tools-chat`, `export`; they are also
packaged under `trajectory_remix/guides/graph-*.md`. Discovery returns schemas
and short descriptions, not every source or the full manual.

## Small correctness checks

```bash
python -m pytest -q tests/test_graph_studio.py tests/test_graph_jobs.py \
  tests/test_graph_foundation.py tests/test_graph_compiler.py tests/test_graph_export.py
npm --prefix tests install
python tests/run_graph_frontend.py
```

The DOM/API suite uses one temporary backend and tiny sources, plus fake preview
timers. It checks dependency edits, both scrub modes, trailing debounce, stale
response fencing/cache, partial arguments, chat framing, invalid JSON, metadata
and Auto/missing-split separation, multiple independent jobs and retained CAS
conflict drafts. API checks exercise source locks, immutable drafts, pinned real
raw/compiled ZIP downloads, import progress/warnings and shared agent commands.
No throughput/scale measurements, training or live LLM calls belong to these
checks. User-machine 25k-stored/20-active and local-model acceptance remain open.
