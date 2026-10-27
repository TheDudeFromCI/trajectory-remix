---
name: trajectory-remix
description: Author curated parallel training trajectories through the Trajectory Remix MCP using synthetic tools, persistent synthetic subagents, invisible sandbox activity, correct timing and loss masks.
---

# Authoring parallel trajectories with MCP

This is the full reference. For a smaller first task, use the
[short starting guide](guides/start.md), or request `get_authoring_guide()`.
Retrieve this complete manual explicitly with `topic: "reference"`.
Before reviewing or repairing, read [the review checklist](guides/review.md)
using `get_authoring_guide(topic="review")`.

Find existing trajectories with `list_trajectories(query=...)`. Its query language
supports spaces (AND), `|` (OR), `-` (negation), parentheses, `*` wildcards,
`#tags`, and `@name`, `@uuid`, `@windows`, `@uses_tool`, `@state`, `@batch` properties. Read
[the search guide](guides/search.md) with `get_authoring_guide(topic="search")`
for examples and precise matching rules.

For "review five candidate trajectories", call
`list_trajectories(query="@state:candidate", limit=5)`. Add `@batch:<batch-id>`
when a batch is specified. Filter before fetching replay content; do not list
every trajectory and check its state manually. Fetch only the matching samples
you will review. Compiler diagnostics use `validate_trajectory(semantic=false)`;
`semantic=true` contacts configured model API endpoints, independently of the
reviewing agent's subscription.

Use this workflow to write a proposed training example. You are the **author** of
the entire scenario: the user, Kidra, the specialists, their replies and external
activity. Kidra is the **coordinator being trained**. Keep those roles separate.
Write each specialist's conversation yourself from its delegated task, retained
history and explicitly shared artifacts. No real agents, execution environment,
provider credentials or shell access are needed for this workflow.

Trajectory Remix is a simulator and editor. File tools modify virtual text in RAM, synthetic subagents consume authored outcomes, and replay reconstructs that state.
Built-in tools do not run code, launch workers or contact providers. Custom tools loaded by the server operator are trusted Python; inspect their schemas and descriptions.

## Connect and discover

1. Call `get_session` for the current revision, tokenizer, special token spellings,
   generation status and window-slot budget. Only the current generation draft
   is read-only; unrelated samples remain editable. Do not guess tokenizer
   spellings or numeric window slots.
2. Call `get_authoring_guide(topic="reference")` to read this file through MCP if you lack repository
   access. Request `topic="example"` from the same tool for the full build-graph
   trajectory JSON in its `content` string. Call `list_simulated_tools` for
   the current synthetic argument schemas.
3. Use `create_trajectory` for a new draft, or `get_trajectory` and `list_events`
   before editing an existing one. Use `expected_revision` on edits. After changing
   event arrays, relist addresses: event indexes are zero-based and can shift.

Connect HTTP clients to `http://127.0.0.1:8765/mcp`, not the dashboard root.
Stdio clients launch `trajectory_remix.mcp_server` with the dashboard root URL.
Browser UIs require an explicit `--mcp-allow-origin` matching their origin.
The transport setup and curl checks are in `docs/MCP.md`. Once connected, all
authoring and inspection below can be done with MCP alone.

## How replay and Kidra's windows work

A trajectory is an authored sequence of events, not a transcript from a running
model. **Replay** compiles those events into a tick-by-tick account of what Kidra
would generate, what outside actors would supply, which windows would be active,
and what the synthetic environment would contain at each point. It must preserve
causality: a tool reply, document revision or subagent finding cannot influence
Kidra before its scheduled arrival. Replay does not actually infer model tokens,
execute code or contact agents. The author supplies the intended model behavior
and external outcomes; Remix checks and reconstructs their timing and state.

### One concept per context window

Kidra is designed around **multiple independent context windows**, rather than
one long conversation. Each active window has its own token history and KV cache.
A **global attention mechanism after every model layer** allows Kidra to use
information across active windows without merging those histories into a single
stream. On a model forward pass, each generating window can emit one token;
other windows may remain idle or receive external content.

Close windows as soon as their useful context is no longer needed, rather than
keeping every window until the final response or closing one while it is still
being consulted. For tool results, keep the tool window open until the relevant
facts have been transferred into reasoning; then generate EOS immediately if no
remaining work needs the original result. Keep reasoning windows open until their
consumers have used them.

Do not call `file_read` to make an already-visible file available to Kidra:
all open Kidra windows already participate in global attention. A read or file
listing needs additional information, such as an unseen file, a changed version,
or directory metadata. Invisible sandbox files are not automatically visible.
For every closure, check the entire downstream generation interval, including
composition of a `chat` request. Closing at its start can remove evidence needed
for later tokens. A sufficient summary retained elsewhere can permit earlier
closure; a promise to remember facts cannot.

Reasoning can run in parallel in multiple windows. Analyze independent files,
constraints, or subtasks concurrently rather than serializing all thought through
one reasoning window. Each branch must still respect observation availability.

Give each window one coherent purpose. For example, keep the system prompt,
current user request, working reasoning, user-visible chat and each attached
document in separate windows. Put each tool interaction in a tool window of its
own, so a large response or long-running operation does not pollute the reasoning
or chat history. Preserve a document or tool window while its information is
needed; close it once it is no longer useful. Cross-window visibility is the
reason Kidra can reason about a tool result without copying the entire result
into the reasoning window.

Every window has a distinct identity. In authored text, `{WINDOW:logical-id}`
references that window; compilation resolves the logical name to its actual
runtime window ID/slot. Kidra refers to windows by those IDs, so a decision such
as "use the result in Window 7" identifies a specific context, not a vague
reference to earlier text. Never invent numeric window IDs: obtain the configured
slots and special tokens from `get_session` and use logical markers in source.
A window's purpose (`window-type`) and its identity are different things.

### File attachments

Put each user file attachment in its own window; do not paste file contents into
the user's chat message. Open attachment windows in the same tick as that chat
message, append the external content with the proper mask, and retain them while
their context is needed. Virtual files accessed through file tools remain a
separate simulator capability.

### Ticks, generation and external appends

A **tick** is one model forward pass, **not** one second. The important distinction
is the source of the text and how many tokens become available at that tick:

- **`generate`** means Kidra is producing the text. Each generating window emits
  at most **one token per tick**. Multiple windows can generate concurrently,
  independently, during the same tick. Kidra's reasoning, intended tool requests
  and final responses belong in `generate` streams and are normally supervised.
- **`append`** means content comes from outside Kidra: a user's message, a
  document, a tool observation or another external actor. The **entire appended
  block is inserted in one tick**, regardless of its token count. It is not
  simulated as Kidra slowly generating that text. External observations are
  normally excluded from the training loss.
- A generated **NOOP** token means **no update** to that window's content/cache
  for the tick. The window remains open; other windows can continue generating
  or receive appended content. Use the tokenizer's configured NOOP spelling.
- A generated **EOS** closes the window and releases its active context. That
  window no longer participates in subsequent global attention. Keep it open
  until any dependent reasoning has actually consumed its contents.

A window begins with the project's configured BOS/header framing, and its
lifetime ends with Kidra's generated EOS. Merely finishing a message, delivering
a tool result or leaving a window idle does **not** close it. Active windows do
not need to produce useful text on every tick; NOOP expresses intentional idle
behavior. The synthetic clock advances by one second per `tick_rate` logical
ticks (default 10). `time_set` and `time_advance` establish Unix/tick anchors;
`time_now` extrapolates from them.

Conceptual replay (tokens shown as placeholders, not literal tokenizer output):

```text
Tick 10  user window:      append entire user request
Tick 11  reasoning window:  generate token A   | tool window: generate token 1
Tick 12  reasoning window:  generate NOOP      | tool window: generate token 2
Tick 13  tool window:       request completed/dispatched
Tick 20  tool window:       append complete tool result
Tick 21  reasoning window:  generate token B, informed by the tool result
Tick 22  tool window:       generate EOS; tool context closes
```

This is a causal illustration, not a mandated event syntax or tool duration.
Within a shared tick, do not rely on an observation becoming visible to a
simultaneous generation step unless the replay's ordering explicitly establishes
that dependency. Scheduling dependent reasoning on a later tick is clearer.

### Tool windows and their lifecycle

The intended tool workflow is **request and observation in the same tool window**:

1. Kidra creates or uses a dedicated tool window and generates the tool request
   there. Other windows (especially reasoning) can continue on their own ticks.
2. At dispatch, the synthetic tool begins its scheduled operation. Long-running
   tool calls occupy logical time but do not freeze unrelated windows.
3. When the operation finishes, its result is **externally appended** to that
   tool window, not generated by Kidra and not copied into the reasoning window.
   Kidra can then refer to that window by ID and reason over the result.
4. After the result is no longer needed, **Kidra generates EOS in the tool
   window** to close it and free the context. A result appearing is not itself a
   closure signal.

In Remix, author visible tool interactions with `create_tool_call` so request,
result, timing and loss masks are compiled together. Do **not** add a duplicate
manual result event. Requests always generate one token per tick, and execution
starts after the final request token. `delivery` controls the result: `"instant"`
(the default) inserts it at completion; `"stream"` delivers it at `tokens_per_tick`
(default 1). The outcome remains a masked external observation in either case.
Use `auto_anchor: true` to capture the final result token for follow-up scheduling.
Specialist `conversationId` values track retained subagent conversations;
they are not window IDs. A resumed specialist turn can use the same conversation
ID without requiring the same window name.

Opening the ordinary `chat` window and reasoning windows is explicit; authored
tool calls arrange their own tool-window setup. Respect
`get_session.max_window_slots`, never overlap two emitting streams in one window
at the same tick, and use separate windows for tool calls that overlap in time.
Reusable tool-window names are safe only when their token streams do not overlap.
Invisible tool events instead change simulated environment state **without any
Kidra-visible tool window or observation**; Kidra learns of such changes only
through a subsequent visible result or read.

When checking replay, inspect `preview_snapshots` for what is visible at each
tick, `get_window_history` for token provenance, masks, NOOPs and EOS lifetimes,
and `get_sandbox_state` for time-correct synthetic side effects. Refer to the
sections below for event schemas, `{N_TICK:...}` anchors, mask settings and
validation steps.

## Plan the example before writing tokens

Decide what behavior the sample should teach, the user's request, the specialist
roles, their dependencies and the intended outcome. A six-specialist software
example might have core, CLI, tests, docs, API review and integration review roles.
The author writes all six histories. Do not run six agents to obtain them.

Keep a private authoring outline for every specialist: task, facts available at
dispatch, artifact versions, reply, elapsed logical ticks, findings and follow-up
answers. A reviewer may discuss source only if its task/history/artifacts supply
that source. A follow-up must build on that conversation's previous answer. Allow
realistic questions, blockers, corrections and differing completion times.

Have Kidra create TODOs with `note_save`, delegate, inspect `runtime_status`, send
progress with `chat`, clarify answers using `resume`, arrange peer review, update
notes and give the final handoff. Kidra should not write implementation code when
the intended lesson is coordination. Represent a specialist's file changes using
invisible tool calls attributed to that specialist.

## Build executable synthetic source

Prefer `create_event`, `create_tool_call` and `apply_batch` for incremental edits.
`apply_batch` accepts `edits: [{name, arguments}, ...]` (at most 100), optional
`dry_run`, `validate` and `expected_revision`. It commits the batch atomically.
`replace_trajectory` is useful for a complete draft or structural repair; always
inspect and validate the resulting source.

Open the named `chat` window before using the synthetic `chat` tool. Open reasoning
windows with meaningful `window-type` values. Tool calls create their tool windows
from tick 0 automatically; reuse a tool window when its token streams do not
overlap. Keep the total distinct windows within `get_session.max_window_slots`.

Deliver Kidra's user-facing messages through a **visible `chat` tool call** with
`arguments: {"content": "message body"}`, after listing `chat` with `show_tools`
in a dedicated tools window. Keep tool schemas out of the system prompt.
Generate the request in a tool window; the simulator inserts the framed message
into the open chat window. Do not duplicate that result or substitute direct
answer generation in chat. Private reasoning belongs in reasoning windows and
must not become another user-facing draft of the same answer.

These are author-side `create_tool_call` arguments for one specialist turn:

```json
{
  "sample_id": "parallel-example",
  "tick": "10",
  "tool": "subagent",
  "window": "coder-call",
  "arguments": { "task": "Implement /math.py with double(value)." },
  "duration_ticks": 200,
  "auto_anchor": true,
  "outcome": {
    "ok": true,
    "result": {
      "conversationId": "coder",
      "response": "Implemented /math.py using numeric multiplication."
    }
  }
}
```

`outcome` is author-supplied scenario data. It is separate from `arguments`, which
is the request Kidra is trained to make. Do not put the answer into Kidra's request.
Do not manually append another request/result for this call: compilation produces
the supervised request and masked observation automatically.

Use different conversation IDs for independent specialists. Reuse the returned ID
through `arguments.resume` for follow-ups; reusing a window name alone does not
continue a conversation. A resumed result must retain the same conversation ID.
For example, after the first turn completes:

```json
{
  "sample_id": "parallel-example",
  "tick": "{N_TICK:1}+2",
  "tool": "subagent",
  "window": "coder-call",
  "arguments": {
    "task": "Which input types are supported?",
    "resume": "coder"
  },
  "duration_ticks": 3,
  "outcome": {
    "ok": true,
    "result": {
      "conversationId": "coder",
      "response": "It follows Python multiplication semantics; the requested contract covers numeric inputs."
    }
  }
}
```

`subagent_get` reveals the retained user/assistant history and status.
`subagent_list` lists identities. The conversation is `running` between dispatch
and completion, then `ready` or `failed`. Unknown, already-running or closed IDs
cannot be resumed. `subagent_close` retains history while preventing further turns.
An authored failure uses `{"ok":false,"error":"Need an API contract"}`; a
dispatched failure retains its ID so Kidra can clarify and retry.

The `assistant` role inside a specialist history belongs to that specialist. Its
reply reaches Kidra as a masked tool observation; it is not Kidra's supervised
assistant output. Read the history and write a coherent next reply yourself.

## Make work parallel without creating real workers

Overlap synthetic call execution intervals using timestamps and durations. Start
coder and docs requests in separate windows, inspect their returned execution
`start`/`end` ticks, then schedule `runtime_status` to finish while both are running.
Allow enough execution time for the status request to finish composing. A later
coordinator `chat` call can describe that observation. Schedule a reviewer after
the required artifact becomes available.

Requests always stream; `delivery` selects instant or streamed results.
Execution starts on the tick after the final request token; `duration_ticks`
starts at dispatch. Inspect `tool_call.start`, `end`, and `result_end` before
scheduling dependent work. The first worked call allocates result anchor 1 in a
fresh sample; use its returned `result_anchor` ID when other markers already exist.
The follow-up begins at `{N_TICK:1}+2`, after its entire result has arrived.
Different windows can compose in
parallel; two emitting streams cannot occupy the same window at the same tick.

For custom streamed requests, `content` or `segments` controls the request text;
`tool` and `arguments` still determine the simulated operation. Keep them
consistent. To teach explicit waiting, add a segment such as
`{"text":"<|noop|><|noop|><|noop|>","loss":{"weight":1}}`, using the project's
configured NOOP spelling. Use `timer` for simulated waits. Cancellation and
background-process launch tools are not implemented. Status comes from scheduled conversations.

Ticks measure token scheduling. The synthetic clock advances at the trajectory's
`metadata.synthetic_clock.tick_rate` (default 10 tokens/s). `time_set` and
`time_advance` store Unix/tick anchors; tools calculate elapsed simulated seconds
from them. Author-side inspection does not advance ticks. Write status messages
that match the observable scenario rather than claiming host wall-clock runtimes.

## Publish external activity invisibly

After the coder's result above, publish its file using its result anchor:

```json
{
  "sample_id": "parallel-example",
  "tick": "{N_TICK:1}+1",
  "tool": "file_write",
  "arguments": {
    "path": "/math.py",
    "content": "def double(value):\n    return value * 2\n"
  },
  "invisible": true,
  "source": "coder"
}
```

This creates an `invisible-tool-call` source event. It changes the sandbox without
request tokens, result tokens, a model window or loss. It does not consume Kidra's
call budget or automatically advance the synthetic clock. It is not permission
to inject information into Kidra's visible context: schedule a visible reply or
file read if Kidra needs to observe the change.

Any registered tool can be invoked invisibly. Invisible arguments are marked
`invisible` in the full author schema and omitted from the public model schema.
Use them only in `invisible-tool-call` events; MCP rejects them in visible calls.
For example, invisible `chat` accepts `username`. Do not add windows, content,
loss settings, delivery options or outcomes to an invisible source event.
`duration_ticks` is a compatibility delay for built-in tools; new tools finish
according to `is_done`. `source` attributes the external actor.

Use `file_stat` / `get_sandbox_state` to obtain a version. Supply pinned references
to reviewers with `arguments.artifacts: [{"path":"/math.py","version":"sha256:..."}]`.
Only those versions and the specialist's retained history enter its context. Later
file edits do not rewrite an earlier review's context. Do not invent hashes or
claim a reviewer checked a newer version it was never supplied.

## Synthetic tool inventory

Always confirm current schemas with `list_simulated_tools`. The 40 built-in tools are local
simulations; a stored skill is text, not installed code.

| Tools                                             | Required arguments and behavior                                                                                                                                    |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `chat`                                            | `content`; append a framed message from `metadata.agent_name` (default Kidra) to the open `chat` window.                                                           |
| `time_now`                                        | No arguments; read the synthetic timestamp.                                                                                                                        |
| `time_set` | All seven fields: `year`, `month`, `day`, `hour`, `minute`, `second`, `timezone`; use `null` for seeded random values. Sets the synthetic clock exactly. Timezone accepts IANA names, UTC or ±HH:MM. See [clock rules](../docs/SANDBOX.md#setting-synthetic-time). |
| `time_advance`                                    | Invisible-only: nonnegative `seconds`; prepare synthetic time without sleeping.                                                                                                    |
| `file_write`                                      | `path`, `content`; save virtual UTF-8 text and return its version; optional `expected_version` prevents stale writes.                                              |
| `file_read` | `path`; optional historical `version`, inclusive 1-based start_line/end_line, and line_numbers. |
| `file_stat`                                       | `path`; return current version and byte size without its body.                                                                                                     |
| `file_patch` | `path` with old/new or an atomic edits array of old/new pairs; each old matches once. Optional expected_version. |
| `file_list`, `file_delete` | List sorted virtual paths, optionally filtered by glob; delete with path. Historical versions remain. |
| `file_copy`, `file_move`, `file_rename` | source/destination; optional overwrite and source expected_version. Move and rename are atomic; all retain historical artifacts. |
| `file_search` | query; optional regex, case_sensitive, glob, context_lines and max_results. Returns bounded line-numbered matches. |
| `memory_save`, `memory_search`                    | Save `key`, `text`; search with `query`, returning up to eight lexical matches.                                                                                    |
| `note_save` | `name`; on creation also `description`, `priority` (`critical`, `very high`, `high`, `medium`, `low`, `very low`), `status` (`not started`, `doing`, `paused`, `blocked`, `done`); optional `blocked_by` task-name array. Updates retain omitted fields. |
| `note_list`, `note_delete` | List the six priority buckets with no arguments; delete by `name`. |
| `skill_save`, `skill_load`, `skill_list`          | Save `name`, `content`; retrieve by `name`; list with no arguments.                                                                                                |
| `subagent`                                        | `task`; optional `resume` and `artifacts`; author supplies the outcome separately. No real delegation occurs.                                                      |
| `subagent_list`, `subagent_get`, `subagent_close` | List with no arguments; inspect/close with `conversationId`.                                                                                                       |
| `runtime_status`                                  | No arguments; inspect synthetic clock, call count, TODO counts and conversation lifecycle summaries.                                                               |
| `calculator` | `expression`; numeric math via simpleeval, including trigonometry, logarithms, roots and constants. Angles use radians. |
| `random` | `kind`: int, bool, float, choice; optional min/max or required choices. Reproducible draws persisted in sandbox state. |
| `timer` | `mode`: timeout/interval with seconds (or explicit ticks); intervals require finite count. Alarm uses at_tick or a timezone-aware synthetic timestamp. |
| `file_watch` | `path`; optional events, count and timeout_seconds (or explicit timeout_ticks). Poll for completed virtual file changes and emit masked observations. |
| `system_diagnostics` | Read simulated CPU, RAM, VRAM, core count, OS and architecture. No host measurements. |
| `system_diagnostics_set` | Invisible-only partial updates to simulated diagnostics. Resource usage must fit totals. |
| `hardware_load_set` | Invisible-only file_io_ticks and per-operation operation_ticks. New file calls sample additive latency once at dispatch. |
| `tool_permissions_set` | Invisible-only permission policy: default allow/deny and ordered tool/path glob rules with decision and optional remaining/message. Simulate user rejection before dispatch. |
| `io_failure_set` | Invisible-only rules for I/O faults by tool/path glob, code, optional remaining/message. Empty rules clears faults. |
| `web_set` | Invisible-only pages with url/title/text, optional published_at/links/status/latency_seconds/error; optional exact-query searches and search_latency_seconds. Replaces active pages; old refs retain snapshots. |
| `web_search` | query; ranked source refs, titles, URLs and verbatim snippets. Optional max_results/offset; use next_offset to continue. |
| `web_open` | Exactly one of url/ref; readable page excerpts and links. Continue with next_line/next_column; paginate links with link_offset/next_link_offset. |
| `web_find` | Exactly one of url/ref plus literal text; matching lines and short context. Optional case_sensitive/context_lines/max_results; use next_line as start_line to continue. |

Web observations default to a 6000-byte JSON-envelope budget (max_output_bytes,
1024–16000), not an exact token count. All three visible tools accept
timeout_seconds (default 30 simulated seconds). Use search refs to pin evidence;
URL reads resolve the current corpus. Seed readable text, never whole HTML pages.
Follow returned link URLs with web_open. Unknown URLs fail without inventing pages.
The old network_set/network_request tools have been removed; reauthor HTTP fixtures
as web pages and research calls. See [web research](../docs/SANDBOX.md#synthetic-web-research).

Generated EOS cancels the owning tool window. A request ending with EOS is never
dispatched, so its arguments may be incomplete. For a running call, schedule a
generate event containing EOS on that window. Phase-B work on the EOS tick can
finish before closure; future execution/results stop, and pending registrations
are cleaned up. Earlier mutations remain. A cancelled call's result-anchor
captures its EOS tick. See [cancellation](../docs/SANDBOX.md#cancellation-with-eos).


Notes are prioritized TODO tasks rather than searchable memories. Use a stable task
`name`, a `description`, a `status`, and a `priority`. `blocked_by` lists other
task names; it is optional and survives updates until explicitly cleared with
`[]`. Self-dependencies and cycles are errors, and a task cannot become `done`
until all blockers exist and are done. Reopening or deleting a prerequisite of
an already-completed task is forbidden.

Every trajectory that calls a note tool automatically receives an always-open
`notes` context window at tick 0, initialized with empty priority buckets.
The compiler replaces its JSON after every task mutation in phase B and masks
all note content from training loss. This reserved window counts toward the
project slot limit; never author a separate `notes` window or generate into it.
Tasks are always visible through this window without calling `note_list`.
Trajectories that never use note tools do not allocate the extra slot.

## Optional author workbench

`create_tool_session` creates isolated ephemeral RAM state with clock and quotas.
`call_simulated_tool` lets you experiment with deterministic file/note tools. For
`subagent`, supply top-level `outcome` exactly as in `create_tool_call`, separate
from `arguments`. Read `get_tool_session` between turns and author the next reply
from the retained context. An omitted outcome produces a failed synthetic turn;
it never triggers a model request. Outcomes for deterministic tools cannot be
overridden. Reset/delete destroys a workbench's conversations.

`record_sample_id` and `record_tool_interaction` record curated request/result
text with appropriate masks, but do not create executable tool-call events or
copy the workbench state into the trajectory. For reconstructable sandbox state
and parallel timing, use `create_tool_call` plus invisible mutations instead.
Do not confuse a workbench's current state with a sample's historical sandbox.

## Masks, markers and curation

- Kidra's intended decisions, reasoning, tool requests and final answer use
  supervised `generate` events. Put user and environment observations in `append`
  events, normally with `loss: {"mask":"exclude"}`. Tool-call results are masked
  automatically; do not override their masks to train Kidra to produce replies.
- Window headers, BOS/EOS and numeric slots come from the project's tokenizer.
  Use `{WINDOW:logical-id}` references, not guessed slot numbers. Write an
  `{N_TICK:1}` anchor into a stream and use its returned resolution for
  dependent events; an anchor only becomes available when its marker is emitted.
  Use `get_anchors` and inspect for unbound or forward references.
- Escape literal markers as `\{WINDOW:...}` / `\{N_TICK:...}` in text (double the
  backslash in JSON). Tool JSON escaping preserves marker-looking data and
  prevents it from becoming model control tokens. Use custom protocol tokens
  only where the intended conversation structure calls for them.
- Add generated EOS for every open window after its last use. Keep useful tool
  observations available until downstream reasoning has consumed them; closing
  a window releases its active KV. Use `get_window_history` to confirm lifetimes.

External real-world logs are research material, not ready-made training samples.
If supplied, rewrite and select their useful content into this scenario. Remove
irrelevant chatter, secrets, scaffolding and unsupported claims; preserve the
facts needed to justify conclusions. Assign each utterance to its proper actor,
visibility and loss mask. Reconstruct state changes with invisible events and
curated visible observations. Never paste a raw subagent log as supervised Kidra
output or treat it as a substitute for temporal/context review.

You may author simulated test results as part of the fictional scenario, provided
the specialist has the relevant context and the reported result is consistent
with the supplied artifacts. Do not present those authored results as tests
actually executed by Remix or as verification of the dataset's real quality.

## Anchor-based scheduling and tool context

Always use tick anchors instead of hard-coded offsets except trivial initial
input timing (for example, a user message at tick 1). Anchors are invisible and
may be placed anywhere in a content block, including mid-sentence:
`I should check what 2123*1565{N_TICK:17} is before proceeding.` Start the
calculator request at `{N_TICK:17}+1` while independent reasoning continues.
Parallelize useful work; do not wait for the entire reasoning block to finish
before issuing an already-justified tool request.

To join multiple branches use `max({N_TICK:7}+1, {N_TICK:8}+1)`. This waits for
all referenced anchors and preserves dependencies when text lengths change.
Nested maxima and numeric operands are supported; cycles, missing anchors, tick
limits, and phase visibility rules still apply. Use +1 when consuming a newly
generated token or completed result, including each branch of a max expression.

Always provide tools context before the agent uses tools, through a `show_tools`
event in an open tools window (for example `{"op":"show_tools","window":"tools","tools":["calculator"]}`).
Do not assume a tool is available to Kidra merely because the authoring API can
list or execute it.
The compiler warns with `tool_not_shown` at the request event if the listing is
missing from visible context. A listing added later, closed, or replaced does
not count. Fix the warning before handing the trajectory off for review.
Use an explicit tools window with `window-type: "tools"` for schemas;
`show_tools` in a system window produces a `tools_in_system` warning. Generating
reply text directly in chat produces `direct_chat_generation`; EOS/NOOP control
events are allowed there. These are advisory compiler diagnostics; inspect and
resolve them before approving a training sample.

## Validate and hand off

1. Call `validate_trajectory` with its default `semantic: false`. This compiles
   locally without a provider. Repair diagnostics and inspect warnings. Optional
   semantic validators are separate configured model calls, not subagents.
2. Inspect `get_sandbox_state` and `preview_snapshots` before dispatch, while calls
   overlap, at completions, and after follow-ups. Running calls must not expose
   future answers/files. Prefer small ranges; snapshots are bounded to 100 frames
   and 4 MB. Large plans can be inspected with narrow ranges/window filters;
   previews do not impose a whole-plan 50,000-row limit. Compiler safety limits
   remain 100,000 ticks and 500,000 expanded rows, including idle-window NOOPs.
3. Use `get_window_history` to check request/output supervision, masked user/tool
   observations, NOOPs, window references and EOS closure. Source structure alone
   cannot prove a reviewer's claims are supported; compare its pinned context and
   authored answer yourself. Recheck dependent claims after editing an artifact.
4. Confirm TODOs, reviews, clarifications, progress messages and final handoff
   agree. Remove repetitive file bodies from visible text; use versions and short
   replies. Keep author instructions and raw specialist logs out of Kidra's loss.
5. If asked to validate, finish edits, reread the current trajectory, and review
   the result. Call `sign_off_trajectory` with `sample_id`, `verdict: "validated"`
   or `"rejected"`, your `reviewer` label, a specific review `summary`, and the
   reviewed `revision` as `expected_revision`. It records your own assessment at
   state 2 or -2 without model API calls. Compilation is required for a passing
   sign-off; warnings remain your responsibility. Later edits invalidate it.
   Ready (3) requires a separate human approval.
6. Call `save_dataset`, then `get_trajectory` for source JSON to return to the
   requester. The UI can export source; the CLI can export training JSONL. Explain
   that this is a curated synthetic sample and report the checks actually done.

The `examples/build-graph-analysis.json` trajectory is a complete
example of invisible file setup, concurrent file tools, separate reasoning
and chat generation, loss masks, and explicit EOS. MCP-only agents can fetch
this full source JSON using `get_authoring_guide(topic="example")`. Parse the
returned `content` string as JSON. This read does not import a trajectory
into the current project. Study its event timing and window separation rather
than treating the synthetic answer as verified code execution.

`examples/persistent-agents.json` is a small synthetic source example of these
mechanics. Extend the scenario's histories and timing
to suit the requested training behavior rather than copying generic replies.


## Tool protocol and schema displays

Open a dedicated window named `tools` with `window-type: "tools"`, then use
`create_event` with `{"op":"show_tools","window":"tools","tools":["chat"]}`
to append selected public schemas to an already open window. Set
`randomly-add-additional-tools: true` to include deterministic extra distractor
tools. Invisible arguments never appear in these schemas.

Every tool, including subagents and custom tools, runs once per tick until
`is_done`. State begins with `tick_rate` (default 10 tokens/s); other keys are tool-owned.
`get_sandbox_state` and preview frames return `sandbox` and `tool_calls` as separate
properties. Do not assume any store exists before its first tool execution.
See [the tool contract](../docs/SANDBOX.md) for custom modules and sparse caching.
