# Source format

A **trajectory source** is one JSON object in the format `kidra-trajectory-graph/1`.
It describes what happens across one or more windows as a graph of events, without
any clock. This page explains the format. Full examples are
[`graph_minimal.json`](../examples/graph_minimal.json) and
[`graph_tools.json`](../examples/graph_tools.json). A JSONL file holds one source per
line.

The authoritative schema is generated from the code:
`GET /api/v1/schema/source`, or in Python `GraphSource.model_json_schema()`.

## The idea

- A **window** is a stream of text with a purpose, such as `chatroom`, `reasoning`,
  `tool` or a document.
- An **event** is one write to one window: the agent says something, something external
  arrives, a tool is called, a result comes back, or a chat message is posted.
- An event lists the events it depends on in `after`. It happens only once **all** of
  them have finished. There are no tick numbers, waits or anchors. Ordering comes from
  dependencies alone, and the compiler turns that into timing when you export.

Map order never matters. Window and event IDs are stable, opaque, nonempty strings,
unique within a trajectory, and used as keys.

## Top-level fields

| Field | Meaning |
| --- | --- |
| `format` | Must be `kidra-trajectory-graph/1`. |
| `id` | The trajectory's UUID. |
| `defaults` | For example `noop_loss_weight` (default `0.1`). |
| `windows` | Map of window ID to window. |
| `events` | Map of event ID to event. |
| `metadata` | Optional free-form JSON for workflow data: stage, split, sign-offs, sampler targets, measured metrics, family, layout. It never affects the source's identity hash or structural checks. |

Parsing is strict: unknown intrinsic fields, wrong types, bad versions and malformed
UUIDs are rejected.

### Windows

| Field | Meaning |
| --- | --- |
| `purpose` | What the window is for. `chatroom` is required for structured chat. |
| `agent_writable` | Whether the agent may write. Agent text and tool calls need a writable window. |
| `reasoning_effort` | Optional. Must name one of the project's effort levels. |
| `intentionally_left_open` | Default `false`. |

A window never reopens after it closes. There is no explicit "open" event: a window
exists from the start, and its header and framing are derived by the compiler. An open
`reasoning` or `tool` window gets an advisory warning unless you tick
`intentionally_left_open`, which only silences that one warning.

### Events

Every event has `kind`, `window`, `after` (default `[]`), `content`, `supervision` and
`close_window` (default `false`). `supervision` is one `masked` flag plus a nonnegative
`weight` (default `1`) applied to the **whole event**, including its framing.

| Kind | Extra fields | Masked by default |
| --- | --- | --- |
| `agent_text` | | no |
| `external_text` | `delivery`, `replace_body` | yes |
| `tool_call` | `call_id`, `tool`, `insert_tool_begin`, `insert_tool_end`, `echo_chat` | no |
| `tool_response` | `call_id`, `delivery`, `replace_body`, `insert_response_begin`, `insert_response_end` | yes |
| `chat_message` | `chat: {"speaker": "…"}`, optional `call_id`, `delivery` | yes |

Masked means excluded from loss but still visible as context. You can override any default.

**Delivery** applies to external text and tool responses: `mode` is `instant` (default) or
`streamed`, and `chain_immediately` (default `true`) says whether the next input can follow
in the same input phase. Chat messages are always instant. These are intrinsic choices, not
arbitrary delays.

**Content** is a literal string, or a list of typed pieces such as
`{"kind":"text","text":"Inspect "}` and `{"kind":"window_ref","window":"document"}`. Window
references use logical IDs, and the compiler later turns them into physical numbers. Text
that merely looks like a control token stays literal. Controls such as BOS, EOS, headers,
message and tool delimiters come from the structure, never from parsing your text.

**Replacing a body.** `replace_body: true` (external text and responses only, with nonempty
content) swaps a window's content while keeping its identity. See
[Compiler](GRAPH-COMPILER.md#replacement-and-closing).

## Tool calls and fragments

Tools here are *authored*: you write the call and its response, and nothing runs. See
[Synthetic tools and chat](TOOLS.md) for the practical side.

- Events sharing a `call_id` use one tool window and must be ordered by dependencies.
- A complete call has both `insert_tool_begin` and `insert_tool_end` (the defaults). To split
  a call, use begin-only, then neither for middle fragments, then end-only. Calls and
  responses can't interleave within a window.
- Tool **end** submits the assembled request. Closing the window before tool end abandons
  the partial request, which needn't be valid JSON. Tool end plus close in one event is fine.
- A response needs an ancestor submitted call and goes to the tool's `response_window`, if
  the catalogue names one. Response content is free-form.
- **Chat echoes** are ordinary `chat_message` events linked by `call_id` to a submitted call.
  `echo_chat` defaults to `true` on calls, and setting it `false` on any fragment turns the
  linked echo off.

Arguments are checked against the tool's JSON Schema (draft 2020-12) only **at submission**,
over the joined fragments, and mismatches are warnings. Local `$ref` works, and external
references are never fetched.

## Diagnostics

`diagnose(source, settings)` and `POST /diagnostics` return diagnostics with `severity`,
`code`, a JSON `path`, optional `event_id`, `message` and `related_events`.

| Severity | Examples | Effect |
| --- | --- | --- |
| **Error** | Missing references, cycles, unordered same-window writers, invalid closure or framing, unknown tools, bad project references | Saving still works. Compilation and export are blocked. |
| **Warning** | Open `reasoning`/`tool` windows, argument/schema mismatches | Never blocks. |

Malformed JSON (a wrong type, say) is rejected before storage. A *structurally* broken
graph can still be saved and edited.

## Project settings the source relies on

Settings are a separate versioned object (`kidra-graph-settings/1`), edited in
[Project settings](PROJECT-SETTINGS.md). It defines the tool catalogue, reasoning levels,
tokenizer profiles, endpoints and roles, review checklists, split ratios and log cache
size. `GET /api/v1/schema/settings` has its schema. Defaults need no tokenizer or endpoint.

## For developers: storage and commands

This section is for code that uses the Python API directly. Most people use the studio,
REST or MCP.

- `GraphStore.initialize(path)` creates a `kidra-graph-project/1` SQLite database (WAL).
  Existing files and unsupported versions are rejected, with no migration.
- `GraphCommands` offers `read`, `create`, `replace`, `patch`, `add_event`, `patch_event`,
  `delete_event`, `set_dependencies` and `remove_dependency`. Patches use RFC 7396 (a `null`
  removes a field, and arrays replace whole). Mutations return `GraphResult(record,
  diagnostics)`.
- **Every edit needs `expected_revision`**, scoped to that trajectory. A `RevisionConflict`
  carries the current revision and commit. Editing another trajectory, or the settings,
  doesn't invalidate it.
- A `SourceRecord` carries the source, `revision`, `commit_id`, `intrinsic_hash` and
  separate intrinsic and metadata revisions. Metadata-only edits make commits but keep the
  intrinsic hash. `read(id, revision)` returns an old snapshot.
- `using_tool(tool_id)` reads an index of current tool usage without parsing sources.
  Invalidation history keeps the latest 1,000 entries, and clients refresh on a gap.
- Run `python examples/graph_edits.py /tmp/example-graph.sqlite` for a self-contained
  example. Code that builds on this should reuse these types, diagnostic codes and
  commands rather than invent a parallel grammar.

Tests for this layer: `python -m pytest -q tests/test_graph_foundation.py`. They use
tiny sources and no timing, model or training runs.
