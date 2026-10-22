# MCP trajectory authoring

Trajectory Remix exposes MCP tools over **stdio** through the official Python MCP
SDK. The bridge connects to the running dashboard over its guarded local API.
The dashboard owns the dataset, tokenizer, generation loop and saves, so browser
and MCP clients share one session.

## Setup

Install the optional MCP dependency in the dashboard's virtual environment:

```bash
python -m pip install -e '.[mcp]'
python -m trajectory_remix \
  --tokenizer examples/demo-tokenizer.json \
  --dataset project.kidra-dataset
```

Configure your MCP client to launch the bridge. Use absolute paths appropriate
for your checkout; the client starts this command as a subprocess:

```json
{
  "mcpServers": {
    "trajectory-remix": {
      "command": "/absolute/path/trajectory-remix/.venv/bin/python",
      "args": [
        "-m", "trajectory_remix.mcp_server",
        "--url", "http://127.0.0.1:8765"
      ]
    }
  }
}
```

On Windows, use the virtual environment's `Scripts/python.exe`. The installed
`trajectory-remix-mcp --url http://127.0.0.1:8765` entry point is equivalent.
Use the dashboard's port if you changed `--port`. Keep the dashboard running;
exiting the MCP client leaves it running. The bridge writes only MCP messages to
stdout. Running it interactively waits for an MCP client on stdin.

Only loopback HTTP URLs (`127.0.0.1`, `localhost`, `[::1]`) are accepted. The bridge
bootstraps the same local session token as the browser, sends it in a header, and
refreshes it after a dashboard restart. Host and Origin checks also apply to
the bridge's API routes. There is no remote hosting or HTTP MCP transport in
this implementation. MCP does not expose endpoint credentials, arbitrary file
paths, shell execution, or provider configuration.

## Tools

All responses are available both as JSON text and MCP `structuredContent`.
Edits return `sample_id`, the new dataset `revision`, and `saved: false`.

| Tool | Purpose |
| --- | --- |
| `get_session` | Read revision, generation status, tokenizer fingerprint and special token spellings. |
| `list_trajectories` | Search IDs/metadata and paginate trajectory attributes. |
| `get_trajectory` | Read complete source JSON, including invalid drafts. |
| `create_trajectory` | Create from full JSON, an empty draft, or `template: "demo"`. |
| `duplicate_trajectory` | Deep-copy source and metadata under a new ID. |
| `replace_trajectory` | Replace complete JSON, including otherwise malformed replay structures. |
| `delete_trajectory` | Remove a trajectory from the session. |
| `get_trajectory_metadata` | Read all top-level attributes except `replay`. |
| `update_trajectory_metadata` | Merge-patch ID, defaults, metadata, schema/policy and other attributes. |
| `list_events` | Read raw events with their current addresses; optionally filter by window. |
| `get_event` | Read one raw event. |
| `create_event` | Insert an event at a numeric or anchor tick, optionally at a list position. |
| `update_event` | Set/remove settings such as window, operation, source, delivery, rate or loss. |
| `move_event` | Move/reorder an event, or shift its numeric tick / anchor offset. |
| `duplicate_event` | Copy an event to another tick/position. |
| `patch_event_content` | Replace text/segments, or apply character-range patches. |
| `delete_event` | Remove an event and its empty tick group. |
| `rename_window` | Rename a window across all events, rejecting collisions. |
| `validate_trajectory` | Compile and optionally run configured semantic LLM validators. |
| `get_diagnostics` | Read compiler errors/warnings, optionally filtered by severity. |
| `get_anchors` | Read resolved anchor ticks, phases and owning event paths/windows. |
| `get_window_history` | Read one window's lifecycle, cumulative text, event spans and token rows. |
| `preview_snapshots` | Read post-commit window state at ticks across an inclusive range. |
| `save_dataset` | Atomically save the session to the dashboard's configured dataset file. |

Tools include JSON input schemas, read-only/destructive annotations, strict type
validation, and rejection of unknown argument names. Errors set MCP `isError`
and include a message, plus a code where applicable (`conflict`, `busy`,
`not_found`, `invalid_arguments`, `unknown_tool`). Compilation errors include
diagnostic paths. A failed `validate_trajectory` is a successful inspection:
it returns `valid: false` with diagnostics.

## Editing rules

- Address trajectories by their stable `sample_id`, not the dashboard's list index.
  IDs must be unique and nonempty. Duplicates receive a UUID unless you specify
  `new_sample_id`.
- Read the current revision with `get_session` or any source read. Pass it as
  `expected_revision` to each edit to reject stale writes. The check covers the
  whole dataset, including browser edits. Each successful edit increments the
  revision once. If you omit it, the edit applies to the latest source.
- Events are addressed by the exact **string** tick key and a zero-based
  `event_index` within that key. Use `list_events` after inserting, moving or
  deleting events. `position` defaults to appending; for moves it indexes the
  destination array **after** removing the original event.
- Edits work on copies and commit atomically. `validate: true` compiles the
  candidate first and rejects the whole edit on an error. The default is false,
  allowing multi-step repairs, incomplete new windows, and temporarily dangling
  anchor references. Compiler warnings do not reject an edit.
- An empty new trajectory is an authoring draft and needs executable events
  before validation can pass. Supply full `sample` JSON to import a trajectory,
  or use `template: "demo"` for a compilable example.
- Generation must be stopped before editing. Read tools remain available during
  generation. Changes remain in memory until `save_dataset` or a dashboard save
  (generation checkpoints also save the shared session).
- The browser reloads a clean selected sample on the next 1.5-second poll.
  If it has pending edits, a conflicting save is rejected and its draft stays
  visible. Copy any text you need before selecting the sample again to reload it.

Metadata uses **JSON Merge Patch (RFC 7396)** against top-level attributes:
objects merge recursively, arrays replace, and null removes a field. `replay`
is excluded. For example:

```json
{
  "sample_id": "example",
  "patch": {
    "metadata": {"title": "Reviewed response", "tags": ["reviewed"], "obsolete": null},
    "defaults": {"implicit-noop-loss-weight": 0.2}
  },
  "expected_revision": 5
}
```

For `update_event`, settings replace individual field values; `remove_fields`
removes optional fields. Use `patch_event_content` for text/segments. Changing an
event's `window` affects that event alone; `rename_window` changes all event
references. Free-form content and metadata are not rewritten by a window rename.
Close windows using a generate event with the configured EOS token, never `close`.

`move_event` accepts either `to_tick` or a signed `tick_offset`. Offsetting
`"12"` by `-2` produces `"10"`; offsetting `"{N_TICK:7}+1"` by `2` produces
`"{N_TICK:7}+3"`. Negative resulting ticks/offsets are rejected. Anchor expressions
remain attached to their anchors. Duplicating events copies marker IDs verbatim;
edit any duplicate anchor definitions before validating.

## Example authoring sequence

Each block below is the arguments object for the named tool. Use the revision
returned by the preceding edit for the next `expected_revision`.

`create_trajectory`:

```json
{"sample_id": "example"}
```

`create_event` to open a window:

```json
{
  "sample_id": "example",
  "tick": "0",
  "event": {"op": "open", "window": "chat", "window-type": "chat"},
  "validate": true
}
```

`create_event` to stream a response (use your `get_session` EOS spelling):

```json
{
  "sample_id": "example",
  "tick": "1",
  "event": {"op": "generate", "window": "chat", "content": "Hello!<|eos|>"},
  "validate": true
}
```

`patch_event_content`:

```json
{
  "sample_id": "example", "tick": "1", "event_index": 0,
  "expected_text": "Hello!<|eos|>",
  "patches": [{"start": 0, "end": 5, "text": "Welcome"}],
  "validate": true
}
```

Patches use Unicode character indexes into the original text, with inclusive
`start` and exclusive `end`. They must not overlap and are applied together;
`start == end` inserts text. For segmented events, use `segment_index` with
patches, or supply the complete replacement `segments` array. Supply exactly
one of `content`, `segments`, or `patches`. Replacing text removes segments and
vice versa; other settings and event-level loss remain intact.

Then call `validate_trajectory`, `get_window_history` with `window: "chat"`,
`preview_snapshots` for a range within `0..max_tick`, and `save_dataset`.

## Inspection and limits

`validate_trajectory` and `get_diagnostics` use the tokenizer-aware compiler.
It stops at its first error. Repair that error and run validation again to find
the next. Open-window warnings are expected for unfinished drafts. Semantic
validation is opt-in with `semantic: true`; it uses the dashboard's configured
validators and can contact provider endpoints. Reports do not alter the sample.

`get_window_history` returns cumulative durable text through `end_tick`, even
when `start_tick` is later than zero. Its events overlap the selected range, and
its token rows are restricted to the range. NOOP rows are excluded unless
`include_noops: true`. `offset`/`limit` paginate rows; `total_rows` gives the
filtered total. Control tokens remain in rows but are omitted from display text.

Snapshots are structured window-state previews, including text, historical
durable-token counts (`cached_tokens`), window status (`pending`, `open`,
`closed`), and live `kv_tokens`. They show state **after phase D** at each
selected tick, including content before the requested range. Closed windows
retain display text and have zero live KV. Bounds are inclusive; `step` samples
`start_tick`, `start_tick + step`, and so on through `end_tick`. `windows` filters
the returned windows.

Preview requests are bounded to 100 frames, 50,000 compiled rows and 4 MB of
frame JSON. Narrow the tick range/filter windows to reduce output; split the
trajectory if its compiled plan exceeds the row limit. List/history page sizes
are bounded to 5,000 entries. Full source reads return untruncated sample JSON.
The existing 40 MB request and compiler tick/row limits also apply.

## Verification

```bash
python -m pip install -e '.[mcp,test]'
python -m pytest -q
python tests/run_frontend.py
```

MCP tests use the official client for initialization, tool discovery, structured
results and error handling, including a real stdio subprocess connected to a
temporary live dashboard. Core authoring tests run without the MCP extra; MCP
protocol tests are skipped when the SDK is absent. DOM tests cover external edits
and pending-browser-draft conflicts. Semantic providers are mocked in tests.

Useful next additions would be atomic multi-command batches, trajectory diffs,
and undo/redo history.
