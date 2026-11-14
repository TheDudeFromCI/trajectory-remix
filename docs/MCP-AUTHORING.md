# MCP authoring reference

How to edit, inspect, review and repair trajectories through MCP. For connecting a
client and the full tool list, see [MCP setup](MCP.md).

## Contents

- [A short authoring session](#a-short-authoring-session)
- [Editing rules](#editing-rules)
- [Scheduling tool calls](#scheduling-tool-calls)
- [Batches and diffs](#batches-and-diffs)
- [Undo and redo](#undo-and-redo)
- [Inspecting results](#inspecting-results)
- [Review and repair](#review-and-repair)
- [Simulated tool workbenches](#simulated-tool-workbenches)
- [Custom guidance](#custom-guidance)
- [Finding trajectories](#finding-trajectories)

## A short authoring session

Each block is the arguments object for the named tool. Pass the revision returned by
the previous edit as `expected_revision`.

1. `create_trajectory`: `{"sample_id": "example"}`
2. `create_event` opens a window:

   ```json
   {"sample_id": "example", "tick": "0",
    "event": {"op": "open", "window": "chat", "window-type": "chat"},
    "validate": true}
   ```

3. `create_event` streams a reply. Use the EOS spelling from `get_session`:

   ```json
   {"sample_id": "example", "tick": "1",
    "event": {"op": "generate", "window": "chat", "content": "Hello!<|eos|>"},
    "validate": true}
   ```

4. `patch_event_content` fixes the wording:

   ```json
   {"sample_id": "example", "tick": "1", "event_index": 0,
    "expected_text": "Hello!<|eos|>",
    "patches": [{"start": 0, "end": 5, "text": "Welcome"}],
    "validate": true}
   ```

Then call `validate_trajectory`, `get_window_history` (`window: "chat"`),
`preview_snapshots` for a range within `0..max_tick`, and `save_dataset`.

Patches use Unicode character indexes into the original text (`start` inclusive,
`end` exclusive), must not overlap, and apply together. `start == end` inserts. For
segmented events use `segment_index`, or supply the whole `segments` array. Give
exactly one of `content`, `segments` or `patches`.

## Editing rules

- **Address by `sample_id`**, not list position. IDs must be unique and nonempty.
  Duplicates receive a UUID unless you pass `new_sample_id`.
- **Use `expected_revision`.** Read the revision from `get_session` or any source
  read and pass it to each edit to reject stale writes. The check covers the whole
  dataset, including browser edits. Without it, edits apply to the latest source.
- **Events are addressed** by the string tick key plus a zero-based `event_index`.
  Re-run `list_events` after inserting, moving or deleting. `position` defaults to
  appending; for moves it indexes the array after the original is removed.
- **Edits are atomic.** They work on a copy and commit together. With
  `validate: true` an edit that fails to compile is rejected. The default is `false`
  so multi-step repairs can pass through temporarily invalid states. Warnings never
  reject an edit.
- **Drafts.** An empty new trajectory needs executable events before it validates.
  Pass full `sample` JSON to import, or `template: "demo"` for a compilable example.
- **Generation must be stopped** to edit. Reads work during generation. Edits commit
  to SQLite immediately, and `save_dataset` only reports a checkpoint.
- **The browser** reloads a clean selected sample on its next poll. If it has pending
  edits, a conflicting save is rejected and its draft stays visible.
- **Metadata** uses JSON Merge Patch (RFC 7396): objects merge, arrays replace, and
  `null` removes. `replay` is excluded:

  ```json
  {"sample_id": "example",
   "patch": {"metadata": {"title": "Reviewed", "tags": ["reviewed"], "obsolete": null},
             "defaults": {"implicit-noop-loss-weight": 0.2}},
   "expected_revision": 5}
  ```

- **`update_event`** replaces individual fields, and `remove_fields` drops optional
  ones. Use `patch_event_content` for text. Changing an event's `window` affects that
  event only; `rename_window` updates all events and unescaped `{WINDOW:id}` markers.
- **Close windows** with a `generate` event containing the EOS token, never `close`.
- **`move_event`** takes `to_tick` or a signed `tick_offset`. Offsetting `"12"` by
  `-2` gives `"10"`, and `"{N_TICK:7}+1"` by `2` gives `"{N_TICK:7}+3"`. Negative
  results are rejected. Duplicated events copy anchor IDs verbatim, so fix duplicate
  anchor definitions before validating.

Each window gets an automatic masked header at opening. `get_window_history` and
snapshots include it in `text` and return `header` and `slot`. See
[Replay format](REPLAY-FORMAT.md).

## Scheduling tool calls

`create_tool_call` adds an executable simulated tool event:

```json
{"sample_id": "example", "tick": "150", "tool": "file_write",
 "arguments": {"path": "/example.txt", "content": "new"},
 "duration_ticks": 25, "window": "write-result", "expected_revision": 5}
```

| Field | Notes |
| --- | --- |
| `tick` | Numeric string or anchor expression. |
| `tool`, `arguments` | See [Tools](TOOLS.md). Use `list_simulated_tools` for exact schemas. |
| `window` | Tool window ID. Generated if omitted. |
| `duration_ticks` | Legacy built-in delay (default 0). |
| `delivery`, `tokens_per_tick` | `instant` or `stream` results, and the stream rate (default 1). |
| `content` / `segments`, `loss` | Custom request text and supervision. |
| `auto_anchor` | Allocate an anchor at the final result token. Returned as `tool_call.result_anchor`. |
| `position`, `expected_revision` | As for other edits. |
| `outcome` | Authored subagent reply (author-only). |
| `invisible`, `source` | Make it external activity with no model tokens. |

Behavior:

- The request streams one token per tick from `tick`, and execution starts the tick
  after its last token. The tool window exists from tick 0, and several calls can
  share it.
- Use `{N_TICK:<result_anchor>}+1` to schedule follow-ups or close the window.
- Omitted request text becomes a canonical request built from `tool` and `arguments`.
  Those structured fields always determine execution. Result text is always masked.
- Runtime failures become `{ok:false,error:…}` observations rather than errors.
- `invisible: true` creates an `invisible-tool-call` event. Any registered tool can
  be invisible. It takes no `window`, request content, delivery or loss, and
  produces no model tokens (tools may still write into open windows). Invisible-only
  arguments are accepted only in invisible calls.
- `create_tool_call` is also usable inside `apply_batch`.

`get_sandbox_state(sample_id, tick, include_calls=true)` returns the opaque `sandbox`
dictionary (tools own its keys) and, optionally, `tool_calls` with resolved
`request_start`, `result_end`, execution `start`/`end`, arguments, and status
(`composing`, `running`, `completed`, `failed`). Output is capped at 4 MB; use
`include_calls=false` for large states.

## Batches and diffs

`apply_batch` applies up to 100 `{name, arguments}` edits atomically. It accepts the
twelve mutation commands from `create_trajectory` through `rename_window`, but not
reads, saves, history actions, simulated-tool commands or repair edits. Put
`expected_revision` on the outer batch only.

```json
{
  "edits": [
    {"name": "create_event", "arguments": {
      "sample_id": "example", "tick": "1",
      "event": {"op": "generate", "window": "new-chat", "content": "OK<|eos|>"}}},
    {"name": "create_event", "arguments": {
      "sample_id": "example", "tick": "0",
      "event": {"op": "open", "window": "new-chat"}}}
  ],
  "validate": true,
  "dry_run": true,
  "expected_revision": 5
}
```

- With `validate: true` (default) the final state is compiled, so intermediate states
  may be invalid. Any failure leaves source, revision and history untouched.
- A successful batch bumps the revision once and creates one undo action, even across
  several samples. Unchanged invalid samples do not block edits to others.
- `dry_run: true` returns prospective results without saving.
- Batch `changes` are RFC 6902 operations on the dataset's **sample array**.

`diff_trajectories` needs `sample_id` and either `other_sample_id` or `other_sample`.
`ignore_id: true` helps compare duplicates, `include_unified: true` adds a text diff
(capped at 200,000 characters, flagged by `unified_truncated`), and `offset`/`limit`
paginate. Diff `changes` are operations on a single sample. Diffs never compile or
mutate anything.

## Undo and redo

`get_edit_history` lists undo actions newest first and redo actions in reapply order
(`include_changes: true` adds the patches). `undo_edits` and `redo_edits` take
`steps` (1–50, default 1) and an optional `expected_revision`. Multiple steps apply
atomically as one revision change, and undo may restore an invalid draft. A new edit
discards the redo branch.

The history is shared by browser edits, imports, generation, MCP edits, batches and
tool-interaction recordings. It keeps up to 50 actions or 32 MB in memory, is lost on
restart, and excludes configuration, credentials, saves and workbench state. Undo
waits for generation to stop, and external changes to a sample invalidate local
history so undo cannot overwrite another writer.

## Inspecting results

- **Diagnostics.** `validate_trajectory` and `get_diagnostics` use the tokenizer-aware
  compiler, which stops at its **first** error. Fix it and validate again to reach the
  next. Open-window warnings are normal for unfinished drafts.
- **Semantic validation** is opt-in (`semantic: true`). It uses the same service and
  configured endpoint as **Edit → Validation → Validate Trajectory**, may contact
  provider endpoints, and never alters the sample.
- **`get_window_history`** returns cumulative durable text through `end_tick`, even
  when `start_tick` is later, with events overlapping the range and token rows limited
  to it. NOOPs are excluded unless `include_noops: true`. Use `offset`/`limit`, and
  `total_rows` for the filtered total.
- **`preview_snapshots`** returns window state **after phase D** at each selected
  tick: text, `cached_tokens`, status (`pending`, `open`, `closed`) and live
  `kv_tokens`. Bounds are inclusive, `step` samples at intervals, and `windows`
  filters. Requests are limited to 100 frames and 4 MB. Narrow the range, raise
  `step` or filter windows if you hit this. Sandbox history is always included, so
  also use `get_sandbox_state(include_calls=False)` for big sandboxes.

Dashboard preview endpoints (`/api/compile`, `/api/frames/{plan_id}`,
`/api/frame/{plan_id}/{tick}`) and their limits are described under
[Storage](STORAGE.md#limits).

## Review and repair

Compiler validation and semantic review are separate. This workflow lets an
external agent review samples without any model API calls from Remix.

**1. Find samples.**

```json
{"name":"list_trajectories","arguments":{"query":"@state:candidate #user_scenario","limit":5}}
```

Generated samples carry their scenario tag (such as `#user_scenario`) and a saved
`metadata.review_checklist` with the original rules and prompts.

**2. Read the review bundle.** `review_trajectory` returns the checklist, review
guide, current `sample`, `context`, compiler diagnostics, `source_hash` and
`revision`. The checklist has `checklist_hash`, every rule's requirement, scope and
applicability, repair guidance, and the `results_schema`. Use
`include_source:false` to omit large sources and read bounded windows instead.
`get_review_checklist` returns just the checklist.

The checklist comes from the sample's saved snapshot when present, which survives
export, import and changed scenario files. Older samples resolve by tag, then
`metadata.scenario`. Manual samples use the common contract, and ambiguous identities
fail rather than being guessed. Read `get_authoring_guide(topic="review")` first.

**3. Repair.** `repair_trajectory` takes `sample_id`, `expected_revision`,
`checklist_hash` and 1–32 `edits`. Each edit is `replace` or `delete` of an original
`event_id`, `insert_before` / `insert_after` it, `append` at a tick, or `replace_text`
with an exact unique `old`, a `new` string and a JSON-pointer `field`. The whole
candidate must compile before one undoable commit. Identity and metadata cannot be
changed this way. `dry_run:true` previews. Saved repairs invalidate earlier
assessments and return `review_required:true`. (These `edits` differ from
`apply_batch` edits, and repair cannot be nested in a batch.)

**4. Record the review.** Re-read the repaired sample, assess **every** rule
independently, then call `record_trajectory_review` with the revision, checklist
hash, your `reviewer`, a specific `summary`, and `results` matching
`checklist.results_schema`. Results are keyed by qualified category (for example
`validate.reasoning`), each with a `checks` object covering every rule's
evidence, status and feedback. The schema is generated dynamically, so do not
hard-code it.

Missing or extra categories or rules, blank evidence, contradictory feedback, stale
revisions and changed hashes cannot approve a sample. Code derives the verdict: all
checks passing records **Validated (2)**, otherwise **Rejected (-2)**. Compiler errors
block a pass but may accompany a rejection, and warnings remain the reviewer's call.
No step grants **Ready (3)**, which needs human approval.

For manual samples, `sign_off_trajectory` records `verdict` (`validated` or
`rejected`), `reviewer`, `summary` and the reviewed `expected_revision`. It stores
those with the source hash and tokenizer/tool fingerprints in `metadata.evaluations`,
does not verify the reviewer, and is invalidated by later content edits.

## Simulated tool workbenches

A workbench is an isolated, in-memory sandbox for trying tools before authoring.
Everything it runs uses the same dispatcher as trajectory replay, so see
[Tools](TOOLS.md) for what each tool does.

```json
{"session_id": "experiment", "clock_seed": "2030-01-01T09:00:00Z",
 "tick_rate": 10, "max_calls": 64}
```

Pass that to `create_tool_session`, then run calls with `call_simulated_tool`:

```json
{"session_id": "experiment", "name": "file_write",
 "arguments": {"path": "/report.txt", "content": "Verified 192 files."},
 "record_sample_id": "example", "expected_revision": 7}
```

- Without `record_sample_id` the call affects only the workbench. The response has an
  interaction ID, the arguments, the outcome and the synthetic clock. `outcome.ok:
  false` is a real simulated failure and can be recorded too. Invalid arguments are
  rejected before running.
- Every call consumes budget and advances the workbench tick (one for instant calls).
  Simulated seconds come from ticks and `tick_rate`, never call count.
  `get_tool_session` does not advance anything.
- **Recording** appends the supervised `<tool>` request and masked `<tool_result>` to
  one tool window (open from tick 0) and commits source and workbench log together.
  It needs a compilable sample or empty draft, and stopped generation. Undo restores
  source only; it never reverses the workbench. `record_tool_interaction` records an
  earlier retained call without re-running it (once per sample, repeatable after an
  undo).
- `chat` needs an open `chat` window, so use `record_sample_id` to run it against a
  trajectory's window. The speaker is `metadata.agent_name` (default Kidra).
- **Limits:** at most 8 sessions, each up to 256 calls, 2 MB of virtual storage and a
  4 MB log. Old log entries are evicted, and everything disappears on shutdown unless
  recorded. In-flight calls block further calls, reset and deletion.
- **No host access.** Virtual paths never touch real files, and there is no shell,
  process execution, arbitrary URL fetch or credentials.
- `time_set` needs all seven fields; see [Sandbox](SANDBOX.md#setting-synthetic-time).

### Authoring subagent replies

`subagent` runs no model. You write the specialist's reply with a top-level `outcome`
on `call_simulated_tool` or `create_tool_call`:

```json
{"session_id": "experiment", "name": "subagent",
 "arguments": {"task": "Implement the parser"},
 "outcome": {"ok": true,
   "result": {"conversationId": "coder", "response": "I used a state machine."}}}
```

Read history with `subagent_get` or `get_tool_session`, write the next reply, and
follow up with `arguments: {"task": "...", "resume": "coder"}` plus a new `outcome`.
Pass `artifacts: [{"path": "/code.py", "version": "sha256:..."}]` to hand the
specialist only those immutable file versions. Replies should reflect only that
specialist's task, history and supplied artifacts. A missing outcome becomes a failed
turn. Test results in replies are authored scenario data; Remix does not run them.
Replies are limited to 200,000 characters.

## Custom guidance

`get_guidance()` returns the exact bundle shown in **View → Trajectory Guidelines**.
Each `documents` entry has an ID, title, content, format, source and `editable` flag.
Pass either `scenario` (prompts and rules before creation) or `sample_id` (the saved
checklist), not both.

Custom Markdown is edited in **Generation Settings → Custom Guidelines** and stored in
the project's `review_guidance` setting: `guide`, `checklist`, and `scenarios`, a map
of scenario name to extra `guide`/`checklist` text (up to 200,000 characters each).
Clearing the text removes the requirement.

Each non-empty custom scope adds a `$custom.dataset` or `$custom.scenario` check with
results `{"valid": true/false, "feedback": [...]}`. Custom text is part of
`checklist_hash`, so a changed requirement makes older reviews and repairs fail.
Dataset-only requirements can be reviewed without any scenario.

## Finding trajectories

`list_trajectories` uses the same query language as the dashboard (see
[Dashboard guide](DASHBOARD.md#searching) and the
[full syntax](../trajectory_remix/guides/search.md)). Useful recipes:

- `@state:candidate` lists samples awaiting external review.
- `@generation:unfinished` lists the same drafts as the resume queue, including
  checkpoint problems, and excludes known manual copies.
- `@generation:complete @state:pending` finds finished generations awaiting review.
- `@errors:false @warnings:false` finds trajectories that compile cleanly.

Matching is case-insensitive, and filters apply before `offset`/`limit`. Metadata
queries (`query_trajectories`, `get_metadata_counts`) are described in
[Storage](STORAGE.md#metadata-and-queries).

`get_session` also reports `max_window_slots` and `slot_policy`. Every authoring path
rejects a trajectory that declares too many distinct windows.

### Generation evidence

`get_generation_record(sample_id, request_index?, include_failures=false, recovery_id?)`
shows a sample's original requirements, bounded request summaries, selected raw
responses and retained construction failures, without running inference.
`get_generation_analytics()` compares candidate retention, inference counters, external
repairs and Ready approvals by generation mode. With a `recovery_id` you can inspect an
archived pre-recovery version, which is historical evidence only; repair and review
the current saved trajectory instead. See [Scenario generation](SCENARIOS.md).

### Compiler warnings worth knowing

- `tools_in_system`: `show_tools` was placed in a system window. Use a dedicated
  window with `window-type: "tools"`.
- `direct_chat_generation`: reply text was generated straight into chat. Send replies
  through the visible `chat` tool. EOS and NOOP in chat are fine.

Neither fails local compilation, and each includes the source event path. Insert
tool schemas with `create_event`, for example
`{"op":"show_tools","window":"tools","tools":["chat"],"randomly-add-additional-tools":true}`.
