# Project storage and limits

This page is for people who want to know how a project is stored, how to query it,
and what the size limits are. You do not need any of it for everyday authoring.

## The project file

A project is an SQLite database, conventionally named `project.sqlite`.

- The `project` row holds configuration, schema and revision counters, the embedded
  tokenizer JSON, the special-token spellings, the window-slot limit and the slot
  allocation policy. The tokenizer changes only through
  [**File → Migrate Tokenizer…**](GETTING-STARTED.md#change-a-projects-tokenizer).
- Each trajectory is a JSON record in its own row, keyed by an immutable **UUID**.
  The editable `id` is only a unique label. The UUID survives edits, undo and
  imports, and seeds window-slot allocation. Duplicating creates a new UUID. Legacy
  imports without a UUID get one derived from their ID.
- The server reads trajectories on demand and never loads the whole dataset.
- API keys are excluded from the project: provider keys stay in server memory, and
  environment-variable credentials are never exported. A project does keep prompts,
  task order, endpoint URLs and model names, generation settings, sample metadata,
  tags and loss controls.

Writes use WAL mode, a 30-second busy timeout and transactions. Generation log
entries and their checkpoints are committed together.

### Backups and sharing

- Several server processes can share one local database. Revisions detect competing
  edits, and unique constraints prevent duplicate IDs.
- Keep the database on a **local filesystem**. WAL databases may have `-wal` and
  `-shm` companion files.
- To copy a project, use SQLite's backup API or stop all writers first. A backup
  also keeps recovery archives and generation journals that a single exported
  sample does not.

### Imports and exports

Imports are limited to 32 MB per file and 40 MB per request, so split large imports
into batches. The dataset itself has no 100,000-trajectory limit. Bulk replacement
imports clear undo history so the old data need not be held in memory. Token exports
process one trajectory at a time and spool to disk, so one invalid trajectory cannot
produce a partial file. See [Export](EXPORT.md).

## Metadata and queries

Each trajectory has a metadata row keyed by UUID: its properties as JSON, a
dedicated tag list, and cached event, window and tag counts. A canonical
`content_hash` supports cache invalidation. Source, hash, metadata and tag index
are updated in one transaction, including undo and redo, and deleting a trajectory
removes its metadata. New metadata keys need no schema change.

`POST /api/metadata/query` (MCP `query_trajectories`) filters without fetching
replay data:

```json
{
  "filters": [
    {"key": "quality", "op": "gte", "value": 0.8},
    {"key": "counts.windows", "op": "lte", "value": 16}
  ],
  "tags": ["reviewed"],
  "tag_mode": "all",
  "offset": 0,
  "limit": 100
}
```

- Operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `exists`. Filters combine with AND.
- Keys are literal metadata property names. `counts.events`, `counts.windows` and
  `counts.tags` read the cached counters.
- A missing key never matches a comparison. JSON `null` matches `eq: null`, and
  booleans are distinct from numbers.
- `tag_mode` is `all` or `any`.

`POST /api/metadata/counts` (MCP `get_metadata_counts`) groups matching trajectories
by a metadata key, a counter, or `tags`, returning a bounded number of value/count
groups. Each trajectory counts once per distinct tag. For example,
`{"key":"domain","tags":["reviewed"]}` counts reviewed trajectories by domain.

Tag and counter lookups are indexed. Arbitrary property filters scan the small
metadata projection, which is fast for typical projects.

## Compilation caching

Compilation is cached in memory to keep generation and previews fast. The caches
never change the dataset format.

- Successful token encodings and the unchanged prefix of the previous timeline are
  reused. Tool scheduling and anchors are resolved on every compile.
- Deterministic built-in tools reuse saved post-call state when their arguments,
  state and inputs are unchanged. External tools and cancellation cleanup always
  execute. Sandbox checkpoints are capped at 16 MB / 1,024 invocations.
- Edits to earlier events rebuild the affected suffix. Replacing content, switching
  trajectory, or changing window allocation forces a full rebuild.
- Generation also caches a small scheduling summary. Plugin tools disable it. The
  final draft is always compiled and validated before being saved.

Python callers can force the reference path with `compiler.compile(sample, incremental=False)`.

## Limits

| Limit | Value |
| --- | --- |
| Ticks per compiled trajectory | 100,000 |
| Expanded rows per compiled trajectory | 500,000 |
| Preview frame / timeline output | 4 MB (valid trajectories stay valid if exceeded) |
| Preview frames per request | 16 (the dashboard asks for 8) |
| Shared plan cache | 6 entries, 500,000 rows total, 15-minute idle expiry |
| Undo history | 50 actions or 32 MB, in memory only |
| MCP workbench sessions | 8, each up to 256 calls and 2 MB of virtual storage |
| Page sizes for list and history reads | 5,000 |
| Import size | 32 MB per file, 40 MB per request |

Preview and compilation are separate: a valid trajectory stays valid even when its
preview is too large to show. The dashboard then explains the limit, and MCP
`preview_snapshots` can still inspect chosen windows and ranges. NOOPs, losses and
exports are unchanged. If the plan cache evicts a preview, the dashboard recompiles
it from source automatically. Cache limits bound retained expansion, not total
process memory.
