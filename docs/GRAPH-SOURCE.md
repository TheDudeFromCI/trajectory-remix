# Graph authoring foundation

`trajectory_remix.graph` provides a tokenizer-free source model, structural
validation, SQLite persistence and application commands. It is a separate new
path: the existing dashboard, CLI, MCP and replay database are unchanged. There
is no old-project reader or migrator. Explicit scheduling/preview and temporary
compiled export are available through the separate [compiler services](GRAPH-COMPILER.md).
REST/studio integration, durable workers, sampling and model calls are subsequent work.

## Source and schema

[Minimal source](../examples/graph_minimal.json) and
[fragmented tool exchange](../examples/graph_tools.json) are complete examples.
One `kidra-trajectory-graph/1` object represents one trajectory; raw JSONL can
carry one such object per line. This foundation does not implement bulk I/O.

The executable schema is `GraphSource` in `trajectory_remix.graph.models`.
Generate its JSON Schema rather than maintaining a second handwritten schema:

```python
import json
from trajectory_remix.graph import GraphSource, ProjectSettings

print(json.dumps(GraphSource.model_json_schema(), indent=2))
print(json.dumps(ProjectSettings.model_json_schema(), indent=2))
```

Parsing uses strict Pydantic DTOs: unknown intrinsic fields, unsupported versions,
wrong types and malformed UUIDs are rejected. Trajectory IDs are canonical UUID
strings. Window/event IDs are stable nonempty opaque strings, scoped to a
trajectory, and serve as map keys. Never infer execution order from map order.
`after` contains predecessor event IDs; all listed dependencies must finish.
Roots have an empty list. Logical windows never reopen after closure.

The primary fields are `format`, `id`, `defaults`, `windows`, and `events`.
`metadata` is an optional JSON object accepting arbitrary annotations, including
unknown nested keys and null values. Omitted metadata stays omitted on dump;
explicit `{}` stays present. Workflow stage, split, signoffs, targets, measured
metrics, families and UI layout belong there. No metadata value participates in
the intrinsic hash or structural validation. Review staleness is determined by
consumers comparing saved source/checklist identities; this layer does not
rewrite signoffs, downstream event content or stages.

A window has `purpose`, `agent_writable`, and optional `reasoning_effort`.
A reasoning effort must name a configured project level when settings are used.
`purpose: "chatroom"` is required for structured chat. `reasoning` and `tool`
windows left open receive advisory warnings. There is no authored Available
window, native physical slot, explicit open event, sandbox or tick anchor.

All events have `kind`, `window`, `after` (default `[]`), `content`,
`supervision`, and `close_window` (default `false`). Supervision has one
`masked` flag and nonnegative finite `weight` (default `1.0`) for the entire
event, including framing. `defaults.noop_loss_weight` defaults to `0.1`.

| Kind | Additional fields | Default mask |
| --- | --- | --- |
| `agent_text` | None | false |
| `external_text` | `delivery`, `replace_body` | true |
| `tool_call` | `call_id`, `tool`, `insert_tool_begin`, `insert_tool_end`, `echo_chat` | false |
| `tool_response` | `call_id`, `delivery`, `replace_body`, `insert_response_begin`, `insert_response_end` | true |
| `chat_message` | `chat: {"speaker": "..."}`, optional `call_id`, atomic `delivery` | true |

Agent text and calls require an agent-writable window. Mask defaults may be
explicitly overridden. `delivery.mode` is `instant` (default) or `streamed` for
external text/responses; chat permits only `instant`. On external events,
`delivery.chain_immediately` defaults to `true`; `false` requests the next input
phase. These are intrinsic source choices, not arbitrary delays. The scheduler
will enforce external-completion versus agent-completion phase boundaries.

`content` is a literal string or a list of typed pieces:

```json
[
  {"kind": "text", "text": "Inspect "},
  {"kind": "window_ref", "window": "document"}
]
```

Reference pieces address logical IDs; the export profile resolves their physical
representation later. Strings resembling control tokens or placeholders remain
literal data. No text parser inserts controls. BOS/NOOP, CTX headers, MSG, EOS and
tool delimiters are derived from structure; this layer does not emit them.
`replace_body` defaults to `false`, is restricted to external text/responses and
must insert nonempty content. It retains the logical window's identity.

## Tool fragments and diagnostics

Calls sharing `call_id` must use one tool/request window and be dependency
ordered. Both begin/end flags default to `true` for complete calls/responses.
For fragments use begin-only, neither for middle fragments, and end-only.
Requests/responses cannot interleave frames within a window. Tool end submits
the assembled request; closing before tool end abandons the prefix without
requiring valid JSON. Tool end plus close is legal (end before EOS). A later
write to a closed logical window is an error.

Each explicit response requires an ancestor submitted request and targets the
catalog's `response_window` when specified. Response content is free-form and
has no output schema. Chat echoes are ordinary `chat_message` events linked by
`call_id`, requiring an ancestor submitted call. `echo_chat` defaults to `true`
on calls; setting it to `false` on any request fragment disables a linked echo.
This foundation neither synthesizes echoes nor executes tools.

`diagnose(source, settings=None)` returns stable `Diagnostic` DTOs:
`severity`, `code`, `path` (JSON field components), optional `event_id`, `message`,
and `related_events`. Missing references, cycles, unordered same-window writers,
invalid closure/framing, unknown tools and invalid project references are errors.
Open reasoning/tool windows and submitted argument/schema mismatches are warnings.
Warnings do not block later compilation; errors do. Malformed DTOs raise
`pydantic.ValidationError` before storage, while structurally broken graph drafts
remain savable and editable.

Tool schemas use JSON Schema Draft 2020-12. Argument/schema checks run at
submission only, against concatenated request fragments. Local `$ref` works;
external references are not fetched. Unresolvable references, recursive validation
failures, non-JSON arguments and symbolic-reference arguments produce advisory
warnings. Editing a request adds no semantic contamination flags to other events.

## Project settings

`ProjectSettings` is versioned `kidra-graph-settings/1`. Defaults require no
training tokenizer or endpoint. It contains:

- `tools`: stable tool ID → `name`, `input_schema` (object/boolean schema), optional
  logical `response_window`. No executable tool implementation or output schema.
- `reasoning_levels`: default `Low`, `Medium`, `High`; labels must be unique.
- `tokenizer_profiles`: stable profile ID → `name`, embedded tokenizer JSON
  `definition`, and `special_tokens` mapping logical names to dedicated integer
  IDs. Computed `fingerprint` hashes the full profile without modifying it.
- `endpoints`: stable endpoint ID → HTTP(S) `url`, `model`, `concurrency` (default
  1), `retries` (default 2). `endpoint_roles` optionally maps `generator`, `fast`,
  `critical` to existing endpoint IDs. These conservative numerical defaults are
  engineering choices. Credentials are supplied separately, outside these records.
- `checklists`: optional `core`/`critical` references. `matrix` and
  `metric_registry` are optional references. Each reference contains `id`,
  `version`, SHA-256 `digest`, and `uri`. This layer validates the record, not
  remote availability or contents.
- `split_ratios`: `train` 0.8, `development` 0.1, `test` 0.1, summing to one.
  `log_cache_mib`: default 1024, nonnegative integer, or `null` for uncapped.
  These are configuration contracts; this foundation does not implement eviction
  or export split allocation.

Required logical controls are `bos`, `eos`, `noop`, `ctx_begin`, `ctx_end`,
`tool_begin`, `tool_end`, `response_begin`, `response_end`, `msg_begin`, `msg_end`,
and `window_ref`. IDs must be distinct and present as dedicated special entries
in the serialized tokenizer's `added_tokens` table. Missing controls reject the
profile; vocabulary is never extended. This static check does not prove encoding
compatibility. The compiler must validate the complete tokenizer and profile
when explicitly used. No tokenizer is constructed on settings or graph reads.

Preview mode/profile/debounce (500 ms default), graph positions and Undo state
are client preferences, excluded from settings and compiler requirements. Export
profile selection remains independent of preview.

## Persistence and commands

Run the self-contained edit example from an installed checkout:

```bash
python examples/graph_edits.py /tmp/example-graph.sqlite
```

`GraphStore.initialize(path, settings=None)` exclusively creates a
`kidra-graph-project/1` SQLite/WAL database. Existing files and unsupported
versions are rejected without migration. Settings and every accepted source edit
have immutable UUID commit identities and immutable revision snapshots.
`GraphStore(path)` opens only this format; it never instantiates a compiler.

`GraphCommands` supplies `read`, `create`, `replace`, `patch`, `add_event`,
`patch_event`, `delete_event`, `set_dependencies`, and `remove_dependency`.
Mutations return `GraphResult(record, diagnostics)`; `has_errors` exposes structural
errors. Source/event `patch` uses RFC 7396 object merge patches: null removes a
field; arrays replace whole. Deleting an event preserves all remaining events
and their dependency strings. Explicitly remove broken references to repair them.

Every edit requires a positive integer `expected_revision` scoped to that
trajectory. A `RevisionConflict` includes `entity_id`, `expected_revision`,
`current_revision`, and `commit_id` for bounded reconciliation. Source identity
cannot change. Editing a different trajectory or project settings does not
invalidate that CAS. Read/copy/validate happens outside write transactions;
short `BEGIN IMMEDIATE` transactions recheck revision and commit snapshot,
current pointer, indexed tool usage and invalidation together. No caller draft
is overwritten by a conflict.

`SourceRecord` exposes the parsed `source`, per-trajectory `revision`, `commit_id`,
`intrinsic_hash`, `intrinsic_revision`, `metadata_revision`, and pinned
`settings_revision`. Intrinsic hashes use canonical, default-resolved primary
fields only. Metadata-only edits create commits but retain intrinsic revision/hash.
`read(id, revision)` retrieves old snapshots. Command diagnostics use the settings
snapshot pinned to that commit, ensuring old reads are stable; explicitly call
`diagnose(source, store.settings().settings)` to assess against current settings.
Project settings have their own CAS and immutable history via `settings(revision)`
and `update_settings(settings, expected_revision)`.

SQL tables are prefixed `graph_`: `project`, `settings_revisions`,
`trajectory_revisions`, `trajectories`, `tool_usage`, and `project_changes`.
`using_tool(tool_id, limit=100, offset=0)` reads the indexed current tool usage
without parsing sources. Invalidation history retains the latest 1,000 entries;
future clients must refresh on a cursor gap. Runtime ownership/worker tables,
review revisions and job transactions can extend this store subsequently.

Downstream compiler and studio code should import these DTOs, stable diagnostic
codes and application commands rather than introduce parallel source grammars.
The foundation does not certify token budgets, compile arrays, provide network
adapters, or implement operational locks; those belong to their respective layers.

## Small checks

```bash
python -m pytest -q tests/test_graph_foundation.py
```

The checks use small sources and concurrent CAS edits, no timing thresholds,
model requests, throughput/load tests or training runs. User-machine live model,
scale and training acceptance remain separate from these correctness checks.
