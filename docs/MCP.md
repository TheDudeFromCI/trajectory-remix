# MCP trajectory authoring

Trajectory Remix exposes MCP tools over **Streamable HTTP** at `/mcp` and over
**stdio**, using the existing official Python MCP SDK. Both transports share the
running dashboard's command handlers, dataset, tokenizer, generation loop and
saves. The stdio bridge connects through the dashboard's guarded local API;
HTTP requests use the same in-process dataset owner.

Start with the [short agent guides](../trajectory_remix/guides/start.md), also available through `get_authoring_guide`. Call `get_authoring_guide(topic="example")` to retrieve the complete bundled example without importing it. Built-in sandbox tools are synthetic; authors supply specialist replies. Custom Python tools are trusted modules configured by the server operator.

## Learn one task at a time

`get_authoring_guide()` returns a short starting page. Request one `topic` for
the next task; it does not concatenate the lessons or load the entire manual:

| Topic | Walkthrough |
| --- | --- |
| `first` | [Create a tiny trajectory; learn start ticks, token streams and closure](../trajectory_remix/guides/first.md) |
| `parallel` | [Overlap independent specialist work and anchor dependent follow-ups](../trajectory_remix/guides/parallel.md) |
| `edit` | [Repair wording and timing with targeted event edits](../trajectory_remix/guides/edit.md) |
| `sandbox` | [Seed trajectory files and verify visible reads](../trajectory_remix/guides/sandbox.md) |
| `tools` | [Choose a small starter tool set and defer advanced tools](../trajectory_remix/guides/tools.md) |
| `reference` | [Complete Skill.md manual](../trajectory_remix/Skill.md) |
| `example` | [Complete build-graph trajectory JSON](../examples/build-graph-analysis.json) |

Existing callers that need the full manual must now pass `topic: "reference"`;
the response still contains `content`. Both transports support the same topics.
Tool pruning is a client/agent configuration recommendation, not a server-side
restriction. Batching, full-source replacement and ephemeral workbench execution
remain available for advanced workflows.

## Setup

Install the optional MCP dependency in the dashboard's virtual environment:

```bash
python -m pip install -e '.[mcp]'
python -m trajectory_remix init \
  --tokenizer examples/demo-tokenizer.json \
  --dataset project.sqlite
python -m trajectory_remix --dataset project.sqlite
```

### Streamable HTTP and llama.cpp Web UI

The endpoint is **`http://127.0.0.1:8765/mcp`**. It is served by the dashboard
when `.[mcp]` is installed; no separate bridge process or new dependency is needed.
Restart the dashboard after installing the extra. Without the optional SDK,
`/mcp` returns `503` with installation instructions and the dashboard still works.

For llama.cpp Web UI at `http://127.0.0.1:8123`, start Remix with:

```bash
python -m trajectory_remix --dataset project.sqlite \
  --mcp-allow-origin http://127.0.0.1:8123
```

Set the UI's MCP transport to `streamable_http`, URL to
`http://127.0.0.1:8765/mcp`, and leave authentication unset for this local service.
The llama.cpp `--ui-mcp-proxy` option may stay enabled. Include `/mcp` in the
upstream URL; the proxy does not turn the dashboard root into an MCP endpoint.
The proxy must preserve POST bodies, Accept/Content-Type, `Mcp-Session-Id` and
`Mcp-Protocol-Version` headers and allow streaming responses.

Direct browser access is allowed only for explicitly configured origins. Use
the actual address shown in the browser: `localhost` and `127.0.0.1` differ.
Repeat `--mcp-allow-origin` for multiple UIs; wildcards and opaque `null` origins
are rejected. Allowed origins receive scoped CORS preflight responses for
GET/POST/DELETE and can read the session headers. An absent Origin is permitted
for local clients such as curl; same-origin dashboard requests are also allowed.
Other cross-origin MCP requests are rejected. The `/api/` dashboard routes retain
their session-token and same-origin requirements; CORS is not enabled for them.
This remains a trusted local, single-user service, not an authenticated public
MCP server. Allowing an origin grants that UI access to the authoring tools.

The SDK handles the initialize/initialized handshake, JSON-RPC requests, session
IDs and protocol negotiation. POST requests accept `application/json` with
`Accept: application/json, text/event-stream`; normal responses are JSON.
Notifications return `202`. A successful initialize returns `Mcp-Session-Id`;
send that header and the negotiated `Mcp-Protocol-Version` on later requests.
Use DELETE to end the transport session. Unknown/expired session IDs return 404,
so initialize again after a server restart. MCP transport sessions are separate
from synthetic `create_tool_session` workbenches, and both share the project.

GET with `Accept: text/event-stream` and a valid session opens the optional SSE
notification stream while POST requests continue to work. There is no legacy
`/sse` endpoint or persistent event replay store.

Test initialization directly, independently of llama.cpp:

```bash
curl -i http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl-test","version":"1"}}}'
```

Expect `200`, a JSON-RPC initialize result and the `Mcp-Session-Id` header. Copy
that ID below (use the response's protocol version if it differs):

```bash
REMIX_MCP_SESSION='paste-session-id-here'
curl -i http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $REMIX_MCP_SESSION" -H 'Mcp-Protocol-Version: 2025-03-26' \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
curl -i http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $REMIX_MCP_SESSION" -H 'Mcp-Protocol-Version: 2025-03-26' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
curl -i http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $REMIX_MCP_SESSION" -H 'Mcp-Protocol-Version: 2025-03-26' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_session","arguments":{}}}'
curl -i -X DELETE http://127.0.0.1:8765/mcp \
  -H "Mcp-Session-Id: $REMIX_MCP_SESSION" -H 'Mcp-Protocol-Version: 2025-03-26'
```

The initialized notification returns `202`; list/call return `200` with JSON-RPC
results. To check CORS, repeat initialization with
`-H 'Origin: http://127.0.0.1:8123'` and inspect `Access-Control-Allow-Origin`
and `Access-Control-Expose-Headers`.

#### Diagnosing proxy errors

Before this endpoint was added, direct `POST /` returned `405`, `Allow: GET`,
and `{"detail":"Method Not Allowed"}` from Remix; `/mcp` returned 404 because
only stdio MCP existed. This reproduces the reported response without llama.cpp.
A proxy's outer `Server: llama.cpp` header alone does not identify who generated
the body. The current root remains a GET-only dashboard; use `/mcp` for MCP.

If the direct initialize/list/call sequence succeeds but the proxied equivalent
fails, compare the proxy's upstream path, method, Origin, Accept, session and
protocol headers with these working requests. The observed `feature_disabled`
403 is not a Remix error payload; inspect llama.cpp's proxy/access configuration
and logs to locate it. An unexpected Origin can also produce a Remix 403 with a
`detail` message; permit the actual UI origin explicitly. We cannot establish
the behavior of a separate llama.cpp installation from direct server tests alone.

### Stdio clients

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
the bridge's API routes. The stdio bridge's `--url` targets the dashboard root;
Streamable HTTP clients instead connect to `/mcp`. There is no remote hosting.
MCP does not expose endpoint credentials, arbitrary file
paths, shell execution, or provider configuration.

## Tools

All responses are available both as JSON text and MCP `structuredContent`.
Edits return `sample_id`, the new dataset `revision`, and `saved: true` (dry runs return `saved: false`).

| Tool | Purpose |
| --- | --- |
| `get_authoring_guide` | Read a short starting guide or one lesson by `topic`; `reference` returns Skill.md and `example` returns the complete build-graph JSON trajectory. |
| `get_session` | Read revision, generation status, tokenizer fingerprint and special token spellings. |
| `query_trajectories` | Filter metadata scalars, tags and counts in SQL; return paginated UUIDs, hashes and summaries. |
| `get_metadata_counts` | Group matching trajectories by metadata values, tags or cached counts. |
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
| `apply_batch` | Apply up to 100 source edits atomically, with final validation and optional dry run. |
| `diff_trajectories` | Compare source JSON against another sample or candidate; return JSON Patch and optional text diff. |
| `get_edit_history` | View the session's available source undo/redo actions and optional patch details. |
| `undo_edits` | Undo one or more source actions as one revision change. |
| `redo_edits` | Reapply undone source actions. |
| `validate_trajectory` | Compile and optionally run configured semantic LLM validators. |
| `get_diagnostics` | Read compiler errors/warnings, optionally filtered by severity. |
| `get_anchors` | Read resolved anchor ticks, phases and owning event paths/windows. |
| `get_window_history` | Read one window's lifecycle, cumulative text, event spans and token rows. |
| `preview_snapshots` | Read post-commit window state at ticks across an inclusive range. |
| `list_simulated_tools` | Discover the simulated tools' exact schemas and per-session availability. |
| `create_tool_call` | Schedule an executable tool event with a shared request/result window open from tick 0. |
| `get_sandbox_state` | Inspect trajectory sandbox state and call statuses at a selected tick. |
| `create_tool_session` | Create an isolated virtual-tool workbench with clock and quotas. |
| `list_tool_sessions` | List workbenches with quotas, clock and state counts. |
| `get_tool_session` | Inspect virtual files, memories, notes, skills and actual-call logs. |
| `call_simulated_tool` | Execute a simulated tool, optionally recording its real call/result on a trajectory. |
| `record_tool_interaction` | Record an earlier actual interaction without executing it again. |
| `reset_tool_session` | Clear a workbench and restore its initial clock/budget. |
| `delete_tool_session` | Destroy a workbench's RAM state and log. |
| `save_dataset` | Report a checkpoint; applied source edits already commit to SQLite. |

Tools include JSON input schemas, read-only/destructive annotations, strict type
validation, and rejection of unknown argument names. Errors set MCP `isError`
and include a message, plus a code where applicable (`conflict`, `busy`,
`not_found`, `invalid_arguments`, `unknown_tool`, `quota_exceeded`). Compilation errors include
diagnostic paths. A failed `validate_trajectory` is a successful inspection:
it returns `valid: false` with diagnostics.

## Complete example trajectory

The [build-graph analysis example](../examples/build-graph-analysis.json)
is bundled with the Python package. Retrieve it without GitHub or local
filesystem access through the existing read-only guide tool:

```json
{"name": "get_authoring_guide", "arguments": {"topic": "example"}}
```

The response contains `content`, a string holding the full source JSON
trajectory, not a summary or a Markdown guide. Parse `content` as JSON
to obtain the sample object. Retrieval does not modify the project or
increment the revision. To import it, pass that parsed object as the
`sample` argument to `create_trajectory`, optionally supplying an
unused `sample_id`. Omitting `topic` still returns the short starting guide.

The scenario uses seven invisible `file_write` calls at tick 0 to seed
six virtual Python modules and `build_config.json`; a user request is
appended at tick 1; two independent `file_list` and `file_read`
operations run concurrently beginning at tick 2. Kidra's reasoning and
chat are generated in separate windows, followed by explicit EOS events.
The response is synthetic example text, not a code execution result.

Compilation depends on the tokenizer used to generate the fixed
start ticks. The repository's character-level demo tokenizer cannot
reproduce the original generated-token durations without retiming.
After importing into a project, run `validate_trajectory` with a
compatible tokenizer and adjust scheduling if necessary.

## Editing rules

### Trajectory sandbox history

`create_tool_call` schedules an executable synthetic tool event in a trajectory.
It accepts `sample_id`, string `tick` (numeric or anchor expression), `tool`, and
an `arguments` object. Optional fields are `duration_ticks` (default 0), `window`
(tool window ID; generated when omitted), `outcome` (recorded subagent
only), `position`, `expected_revision`, `delivery` (`instant` or `stream`),
`content` or `segments` (custom request text), `loss` (request supervision),
`tokens_per_tick` (streamed result rate, default 1), and `auto_anchor` (default
false; allocate an unused post-result anchor ID and persist it as `result-anchor`).

Set `invisible: true` for external sandbox activity and optionally supply `source`
(for example `"user"` or `"coder"`). This creates `op: "invisible-tool-call"` in
source. Every registered tool may run invisibly, without a `window`, request
content, delivery settings, loss or recorded outcome. Such calls have no model
request/result tokens; tools may still write masked content into open windows.
Built-ins skip implicit call-budget and clock increments for invisible calls.
Hidden failures are author-facing compilation errors. Invisible schema arguments
are accepted only in invisible trajectory calls.
Compilation must succeed before one
undoable source edit commits. It is also available inside `apply_batch`. Save
immediately, as with other edits.

```json
{
  "name": "create_tool_call",
  "arguments": {
    "sample_id": "example",
    "tick": "150",
    "tool": "file_write",
    "arguments": {"path": "/example.txt", "content": "new"},
    "duration_ticks": 25,
    "window": "write-result",
    "expected_revision": 5
  }
}
```

The tool window exists from tick 0. The `<tool>` request always emits one model
token per tick starting at `tick`; execution starts on the next tick after its
final token. Unfinished tools run every tick and may update state or emit output
on each invocation. Built-ins retain duration as a compatibility delay.
`delivery` controls `<tool_result>`: `instant` inserts output at its invocation
tick; `stream` queues it at `tokens_per_tick`. State changes occur when reported.
Custom `content`/`segments` can include anchors
and NOOPs to pause a request while a parallel call returns, then resume with its
result. Omitted text produces a canonical request from `tool` and `arguments`;
those structured fields always determine execution. Result text is always masked.
With `auto_anchor: true`, the returned `tool_call.result_anchor` identifies a
phase-B anchor at `tool_call.result_end`, the final result token's tick. Use
`{N_TICK:id}+1` to schedule follow-up calls or close the same tool window.
Multiple calls can share an open tool window. Equal completions use stable
replay order. Use `list_simulated_tools` for exact argument schemas. Runtime
built-in failures become `{ok:false,error:...}` observations. The legacy recorded
`outcome` is author-only context consumed by the built-in subagent; it never
overrides a deterministic tool's actual result.

`get_sandbox_state(sample_id, tick, include_calls=true)` returns `revision`, `tick`
and the opaque `sandbox` dictionary, plus separate `tool_calls` when requested.
The dictionary starts empty; tools own all of its keys. Invisible calls
are marked `invisible: true` and include `source` in this author-facing history.
Each visible call has resolved `request_start`, `result_end`, execution
`start`/`end`, arguments and status (`composing`, `running`, `completed`, `failed`);
composing and running calls omit their future outcome. The output limit is 4 MB;
use `include_calls=false` to omit call logs when inspecting a large state.
`preview_snapshots` also includes sandbox state for each requested tick.

Scheduled calls execute against the trajectory's sandbox. Interactive
`create_tool_session` / `call_simulated_tool` workbenches have independent RAM
state. `record_tool_interaction` retains an actual workbench observation as text;
it does not schedule an executable replay event or copy unrecorded workbench
state. Generated LLM tool interactions automatically use executable events.

### Source editing

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
  generation. Source edits commit immediately to SQLite. `save_dataset` reports
  a checkpoint and never loads or rewrites all trajectories.
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
references and unescaped `{WINDOW:id}` markers in content/segments. Other
free-form content, escaped markers and metadata are not rewritten by a rename.
Close windows using a generate event with the configured EOS token, never `close`.

Every window receives an automatic masked header at opening. Use
`{WINDOW:tool0}` in authored content to refer to a stable logical window ID;
compilation/export resolves the marker to a numeric slot shared with its header.
`get_window_history` and timeline snapshots include these headers in `text` and
return `header`/`slot` in window metadata. Literal markers use `\{WINDOW:tool0}`.

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

## Atomic batches and diffs

`apply_batch` accepts an `edits` array containing `{name, arguments}` objects.
Supported names are the twelve trajectory/event mutation commands from
`create_trajectory` through `rename_window`; the input schema enumerates them.
Read tools, saves, batches, history actions and simulated-tool commands cannot
be nested in a batch. Supply `expected_revision` on the outer batch only.

For example, this batch creates a generated stream and its opener together:

```json
{
  "edits": [
    {
      "name": "create_event",
      "arguments": {
        "sample_id": "example", "tick": "1",
        "event": {"op": "generate", "window": "new-chat", "content": "OK<|eos|>"}
      }
    },
    {
      "name": "create_event",
      "arguments": {
        "sample_id": "example", "tick": "0",
        "event": {"op": "open", "window": "new-chat"}
      }
    }
  ],
  "validate": true,
  "dry_run": true,
  "expected_revision": 5
}
```

The default `validate: true` compiles changed surviving samples at the end, so
an intermediate state may be invalid. Individual edits may also explicitly
request validation. Any failed edit or final validation leaves source, revision,
and history unchanged. Unchanged invalid samples do not block editing another
sample. A successful batch increments the revision once and creates one undo
action, across all affected samples. Intermediate revision numbers are private.

`dry_run: true` validates and returns prospective results/changes without editing
anything. Batch `changes` are RFC 6902 add/remove/replace operations against the
dataset's **sample array**. Diff-tool `changes` are operations against a **single
sample object**. These are inspection outputs; the batch interface accepts named
edits, not arbitrary patches.

`diff_trajectories` requires `sample_id` and exactly one of `other_sample_id` or
`other_sample` (candidate JSON). `ignore_id: true` helps compare duplicates.
`offset`/`limit` paginate operations. `include_unified: true` adds a readable JSON
diff, capped at 200,000 characters with `unified_truncated` indicating truncation.
Diffs work on invalid drafts and never compile or mutate either input.

## Undo and redo

`get_edit_history` lists undo actions newest first and redo actions in the order
they will be reapplied. `include_changes: true` adds each action's forward patch.
Use `undo_edits` / `redo_edits` with `steps` (1–50, default 1) and optional
`expected_revision`. Multiple steps are restored atomically and increment the
revision once. Undo is allowed to restore an invalid draft. New source changes
discard the redo branch. Source changes made by browser CRUD/import, generation,
MCP edits, batches and tool-interaction recording share this history.

The dashboard also has **Edit → Undo / Redo**. It persists pending source edits
before undoing them. During an active compilation, wait for it to finish.
Outside focused text editors and open dialogs, Ctrl/Cmd+Z undoes, and
Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redoes. Text inputs retain native text undo.

History retains up to 50 actions / 32 MB of serialized history entries in RAM.
Old or oversized actions may be evicted, with a notice in `get_edit_history`.
History resets on restart and is not written to datasets. Configuration changes,
credentials, saves, and virtual-tool session state are excluded. Undo/redo must
wait until generation is stopped; each undo/redo commits immediately to SQLite.
External sample changes invalidate local history, so undo cannot overwrite another
writer. Bulk replacement imports clear history to keep memory bounded.

## Simulated Remix tool sessions

These commands use the same `Sandbox` dispatcher as trajectory generation:

- Chat messages: `chat` with `{"content":"Hello!"}`. It requires an open
  window named `chat`. For `call_simulated_tool`, supply `record_sample_id` to
  use that trajectory's open chat window and agent name, and record the message
  as an undoable edit. Standalone sessions have no chat window and return an
  error observation. Scheduled `create_tool_call` events check the window at
  completion. `metadata.agent_name` supplies the speaker (default: Kidra), and
  `metadata.user_name` can store the user's name.
- Synthetic clock: `time_now`, `time_advance`, `time_set`.
- Virtual RAM filesystem: `file_write`, `file_read`, `file_list`, `file_delete`.
- Versioned artifacts: `file_stat`, `file_patch`; writes return a `version`.
  `file_read` optionally accepts a pinned `version`, and writes/patches accept
  `expected_version` to reject stale changes. Old versions remain readable after
  editing or deleting the current file, within the session's storage quota.
- Lexical memory search: `memory_save`, `memory_search`.
- Notes/tasks: `note_save`, `note_list`, `note_delete`.
- Text skills: `skill_save`, `skill_load`, `skill_list`.
- Authored synthetic delegation: `subagent`; the author supplies its outcome.
- Conversation inspection: `subagent_list`, `subagent_get`, `subagent_close`.
- Coordination status: `runtime_status` returns synthetic time, completed call
  count, task counts and subagent identities/statuses.

Start with `list_simulated_tools` to read exact names, required arguments and
availability. Create a workbench with `create_tool_session`, for example:

```json
{
  "session_id": "experiment",
  "clock_seed": "2030-01-01T09:00:00Z",
  "tick_rate": 10,
  "max_calls": 64
}
```

Clock and call-budget defaults come from the dashboard configuration. Sessions
are independent of each other and of generation sandboxes. They are not tied
to a sample ID; record their interactions into any suitable trajectory. Virtual
paths never access host files. Sessions do not expose shell commands, process
execution, arbitrary URL fetching, or host credentials.

`call_simulated_tool` can execute and record a call together:

```json
{
  "session_id": "experiment",
  "name": "file_write",
  "arguments": {"path": "/report.txt", "content": "Verified 192 files."},
  "record_sample_id": "example",
  "expected_revision": 7
}
```

Without `record_sample_id`, the call changes only the workbench. The response
contains an interaction ID, the actual arguments, outcome and synthetic clock.
`outcome.ok: false` represents an actual simulated failure (for example a missing
file) and may also be recorded. Invalid arguments are rejected before execution.
Every dispatched workbench call consumes call budget and advances the workbench's
logical tick (one tick for instant calls). Simulated elapsed seconds are derived
from that tick and `tick_rate` (default 10 tokens/s), never from call count.
`time_advance` additionally shifts the synthetic anchor by its requested seconds.
Source inspection with `get_tool_session` does not advance ticks or consume calls. `time_set` replaces the clock with supplied
local date/time fields, randomizing explicit nulls deterministically. All seven
fields are required; see [synthetic time rules](SANDBOX.md#setting-synthetic-time).

Recording appends supervised `<tool>` request text followed by masked
`<tool_result>` content in the same tool window, open from tick 0. The response's
`call_window` and `result_window` identify that same window. Results remain open
for subsequent trajectory reasoning; close them
later with generated EOS if appropriate. Marker-looking strings and special
tokens in tool JSON are escaped as literal data while preserving their decoded
values. Recording requires a compilable sample or a new empty draft and stopped
generation. Source and virtual state/log commit together; a rejected recording
leaves them unchanged. Undoing a recording restores source only, so it never
silently reverses a tool experiment.

Use `record_tool_interaction` with `sample_id`, `session_id` and `interaction_id`
to record a retained earlier result without consuming another call. Recording
the same interaction twice in one sample is rejected. A recording removed by
undo may be recorded again from the retained log.

`get_tool_session` returns RAM state and the call log; `include_state` and
`include_log` can reduce its output. `list_tool_sessions` gives summaries.
`reset_tool_session` clears state/log and restores the initial clock and budget;
`delete_tool_session` destroys it. There are at most eight sessions, each limited
to 256 calls, up to 2 MB of virtual storage, and a 4 MB retained call log. Old log
entries are evicted, so record useful interactions before they expire. Session
state and logs disappear on server shutdown and are excluded from dataset saves
unless explicitly recorded as trajectory events. In-flight calls prevent another
call, reset or deletion of that workbench; cancellation preserves prior state.

`subagent` is available without any configured endpoint. Supply an authored
`outcome` at the top level of `call_simulated_tool`, separate from the model's
`arguments`:

```json
{
  "session_id": "experiment",
  "name": "subagent",
  "arguments": {"task": "Implement the parser"},
  "outcome": {
    "ok": true,
    "result": {"conversationId": "coder", "response": "I used a state machine."}
  }
}
```

Read retained history using `subagent_get` or `get_tool_session`, then author the
next response yourself. Follow up with `arguments: {"task":"Why that choice?",
"resume":"coder"}` and another authored `outcome`. Replies should reflect only
that specialist's task, retained history and explicitly supplied artifact versions.
Independent calls create independent conversations. The author may choose an ID;
otherwise Remix assigns one. No model/provider request or real agent launch occurs.
A missing outcome becomes a failed synthetic turn; malformed outcomes are rejected
before workbench dispatch. Deterministic tools reject authored outcome overrides.
For timestamped executable replay, supply the same outcome to `create_tool_call`.
The generation tool loop advertises every public tool schema, including `subagent`; the built-in subagent does not invoke nested providers;
authored seed conversations remain available for inspection by ordinary tools.

Optional `artifacts` is an array of `{"path":"/code.py","version":"sha256:..."}`
references. Only those immutable versions are resolved into the delegated context;
the parent's other RAM state and trajectory context are excluded. This keeps source
prompts and compiled requests compact while supplying reviewers with actual source.
`subagent_get` accepts `conversationId`; list/get reveal retained history and
statuses (`running`, `ready`, `failed`, `closed`). Close retains history and prevents
further resumes. A failed dispatched turn returns its `conversationId` alongside
`ok: false` and `error` for inspection or retry. Resuming an unknown, running or
closed conversation fails. Replay
checks availability at dispatch, so a later completion cannot justify an early
resume. Parallel recorded conversations remain independent.

`note_save` now accepts optional `owner`, `depends_on`, `blockers` and
`acceptance_criteria`; the latter three are string arrays. Status also accepts
`blocked`. Omitted optional fields are retained on update; use an empty string or
array to clear them. Self-dependencies/cycles are rejected. Marking a task `done`
requires all dependencies to exist and be done, with no blockers. Acceptance
criteria are recorded requirements, not executable evidence checks.

Synthetic subagent responses are bounded to nonempty text of at most 200,000
characters and recorded outcomes to 200,000 bytes. Conversation state and artifact
versions count toward storage quotas. Parallelism is represented by overlapping
scheduled calls, not a background worker registry. Test results can be authored
scenario data; Remix does not execute them or verify arbitrary prose claims.
See [sandbox behavior](SANDBOX.md) for timing and compatibility details.

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

Tests also cover batch rollback and final-state validation, JSON Patch roundtrips,
history across browser/generation/MCP edits, sandbox isolation, call budgets,
literal marker content, authored outcomes, recording, busy workbenches and revision conflicts.

## Querying metadata and project constraints

`get_session` includes `max_window_slots` and `slot_policy`. The slot budget is
fixed during project initialization. All authoring paths reject a trajectory
that declares too many distinct windows, including implicit tool windows.
The immutable trajectory `uuid` seeds `uuid-hash-v1` allocation; `id` is an
editable unique label. UUID changes are rejected; duplicates receive new UUIDs.

`query_trajectories` accepts `filters: [{key, op, value}]`, `tags`, `tag_mode`
(`all`/`any`), `offset`, and `limit`. Scalar filter operators are
`eq/ne/lt/lte/gt/gte/exists`. Keys are literal top-level metadata keys, except
reserved `counts.events`, `counts.windows`, `counts.tags`. Missing properties
do not match comparisons; JSON null is distinct from an absent property and
booleans are distinct from numbers. Results include UUID, content hash, metadata,
tags and cached counts, with no replay payloads.

`get_metadata_counts` takes the same filters/tags, plus `key` (default `tags`)
and `limit`. It returns bounded value/count groups and total matching trajectories.
Tag groups deduplicate repeated tags within each trajectory. Objects/arrays remain
stored metadata but are not grouped as scalar values.


## Generic tool protocol

The sandbox is an opaque dictionary initialized with tick_rate (default 10 tokens/s). `get_sandbox_state`,
`preview_snapshots`, and `/api/samples/{index}/sandbox/{tick}` keep `sandbox` and
`tool_calls` separate. No store or clock key is guaranteed to exist. All tools
use the same per-tick run protocol; unfinished tools can emit output and mutate
state on multiple ticks. Built-in `duration_ticks` remains a compatibility delay.

Any tool may be scheduled with `invisible: true`. Full author schemas may include
properties marked `invisible`; these are allowed only in invisible trajectory
calls. Visible event editing and `call_simulated_tool` reject such arguments.
The old list of external mutation tools no longer restricts invisible calls.

Insert selected public schemas using `create_event` with an event like
`{"op":"show_tools","window":"system","tools":["chat"],"randomly-add-additional-tools":true}`.
This is a masked text append to an already open window. The checkbox editor
supports the same fields. See [the tool contract](SANDBOX.md) for custom Python
modules, state snapshots, and external schema/hash dependencies.
