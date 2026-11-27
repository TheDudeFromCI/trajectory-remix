# Studio reference

This page covers the studio's editing controls, previews, import and export, search,
history, and its REST and MCP interfaces. For setup and day-to-day use, see the
[Dashboard](DASHBOARD.md).

The studio sits on top of the [source format](GRAPH-SOURCE.md), the
[compiler](GRAPH-COMPILER.md) and the [job service](GRAPH-JOBS.md). Screenshots:
[graph view](images/graph-studio.png) and [Tick history](images/graph-studio-ticks.png).

## Editing a trajectory

Create a trajectory, add windows and events, and press **Save**. The
[Dashboard tutorials](DASHBOARD.md#tutorials) walk through it step by step.

- **Drafts can be broken.** Diagnostics point to stable event IDs and source paths, and
  saving is never blocked. Deleting an event leaves any references to it in place, and
  each dependency chip's remove button clears one explicitly.
- **Same-window writers must be ordered.** Two events writing one window need one
  to depend on the other.
- **Conflicts keep your text.** Edits use per-trajectory revision guards and operational
  locks. A save from an older revision is merged onto newer saves when they changed
  different values; only a change to the same value is a conflict. After a conflict,
  **Reload saved source** is an explicit step.
- **Duplicating copies the saved version.** **More → Duplicate trajectory** creates a
  copy with a new ID (same windows, events and metadata) and opens it. Unsaved edits
  stay on the original.

Select an event to edit:

| Control | Effect |
| --- | --- |
| Window and dependencies | Add a dependency with the searchable picker, or Shift-click a predecessor in the timeline. Hover shows an event's dependencies, and a chip's remove button deletes an edge. |
| Mask and loss | Whole-event supervision. |
| Tool request and response begin/end | Build fragmented calls and responses. Content may be intentionally partial JSON. |
| Close window, replace, delivery, chain immediately | Structural controls. |
| Chat speaker | A speaker field. Message framing is derived. |

Bordered uppercase labels in the UI are visual only, never text inserted into your
content.

**Tool argument form** replaces the content box with one field per argument from the
tool's input schema: a text box for strings, a number box for numbers, a dropdown for
booleans and fixed choices, and a small JSON box for objects, arrays and arguments the
schema doesn't list. Required arguments are marked, and clearing an optional field
removes it. If the content isn't a complete JSON object yet, the text box stays and the
text is left untouched when you switch between raw and form modes.

**Metadata** is separate from the intrinsic source. A missing split warns, while an
explicit **Auto** is intentional. The event editor's **JSON** tab keeps arbitrary nested
annotations. Changing stage or split doesn't change a trajectory's identity.

## Preview modes

- **Dependency graph** mode (default) needs no tokenizer or scheduler. Scrubbing walks a stable
  dependency order. It is **not authored time**.
- In both modes, windows changed by the step into the current position get a faint
  outer glow. It is purely visual and updates as you scrub.
- **Tick history** is opt-in. It runs the real compiler scheduler with the tokenizer
  profile you choose in Preferences, after a trailing debounce (default 500 ms,
  configurable from 0 to 10,000 ms).

Tick previews include unsaved drafts, write nothing, and show at most 1,000 frames
in the browser (the API allows 1 to 5,000), with a label when truncated. Edits reset the
timer and discard stale responses, so an old result can't replace a newer draft. The
server caches up to 16 entries / 8 MiB, and the browser reuses up to four responses. The
export profile never depends on the preview preference.

Window cards render the previewed tokens in a readable view (chat bubbles, formatted JSON,
one `NOOP ×N` chip per run of idle predictions) or, with **Raw**, as the exact token
stream. Both views read the same tokens; neither changes the source or compiled output.
NOOP rows are model emissions, never context, so they don't add to a window's text. See
[Reading windows](DASHBOARD.md#reading-windows).

## Import, export and jobs

- **Import** accepts graph JSON, an array, or JSONL, up to 40 MB per request. It runs as
  a durable job with a per-item atomic commit. Invalid items and UUIDs that already exist
  are skipped with a log warning, and existing sources are never overwritten. Split
  bigger uploads yourself. Pausing or restarting waits for a manual Resume, and cancelling
  keeps what was committed.
- **Export** starts a real, pinned job: Ready is the default minimum stage, stale is
  included, and you choose shard size, split handling and metadata projection. Finished
  jobs list the manifest, shards, profile files and a ZIP. Details in [Export](EXPORT.md).
- **The Jobs view** lists jobs 50 per page and keeps one detail panel per inspected job.
  Logs page 100 lines at a time, keep up to 200,000 characters of text in the browser, and
  clear or export from the server's retained logs. The status bar shows a chip for each
  active job among the newest 50.

## Search, bulk actions and history

The sidebar search filters by **text**, **stage**, **split** and **tool**, using the
same selector as bulk jobs. A bulk action applies to **every** match across all pages,
and it reports trajectories skipped because they were locked or had changed.

When a bulk job settles, it leaves one local **Undo** action with the actual changes.
**Redo** restores the saved results, and neither invokes a model. Individual edits and
deletions have revision-guarded history too. History is per browser session, capped at
50 actions. Save or reload any unsaved draft before using it.

## Calling the studio from code

All dashboard operations use REST under `/api/v1`. Requests need the `X-Remix-Token`
header that the studio injects into its HTML page, and the usual localhost Host checks
and a strict content security policy apply. Tokens are never put in download URLs.

| Route | What it does |
| --- | --- |
| `GET /project`, `GET`/`PUT /settings` | Project identity, and versioned settings (`{expected_revision, settings}`). |
| `GET /settings/default-review-checklists` | The packaged Core and Critical review criteria, used by the dashboard's reset action. Read-only; it never changes settings. |
| `GET /schema/source`, `/schema/settings` | JSON Schema generated from the real models. |
| `GET /trajectories` | List with `text`, `stage`, `split`, `tool`, `limit`, `offset`. |
| `POST /diagnostics` | Validate a draft without saving or tokenizing. |
| `POST /trajectories` | Create one. Returns 201 with revision and diagnostics. |
| `GET /trajectories/{id}?revision=` | A current or historical record, plus its current lock owner. |
| `PUT`, `PATCH`, `DELETE /trajectories/{id}` | Replace, RFC 7396 merge patch, or delete (all revision-guarded). A stale `PUT` merges onto newer saves; overlaps return 409 with `conflicts`, settled by resending with `resolutions: [{path, keep: "ours" \| "theirs"}]`. |
| `POST`, `PATCH`, `DELETE /trajectories/{id}/events/{event}` | Edit one event by stable ID. |
| `PUT /trajectories/{id}/events/{event}/dependencies` | Replace its `after` list. |
| `GET /trajectories/{id}/graph`, `/diagnostics` | Tokenizer-free order and diagnostics. |
| `POST /trajectories/{id}/preview` | Read-only Tick history for a draft. |
| `POST /imports` | `{text}`. Returns 202 with a job. |
| `GET /job-summaries`, `/jobs/{id}/manifest` | Compact job list, and a finished export's manifest. |
| `GET /agent/guide/{topic}` | One short agent guide. |

Job, bulk, log, export and download routes are listed in [Jobs](GRAPH-JOBS.md#rest-routes).
Errors follow one convention: **422** for invalid JSON or fields (with locations),
**409** for revision, ownership or stale-worker conflicts, and **404** for missing
records or expired artifacts. Structurally invalid graphs return diagnostics instead of
an error, so they stay editable.

For agents, [MCP](MCP.md) offers matching tasks (`get_graph_session`, `list_graphs`,
`view_graph`, `update_event`, `export_graphs` and more). It calls the same services
as the studio and has a focused guide per topic: `start`, `dependencies`, `tools-chat`,
`generation` and `export`.
