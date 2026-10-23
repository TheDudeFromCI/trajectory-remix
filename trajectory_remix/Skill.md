---
name: trajectory-remix
description: Author curated parallel training trajectories through the Trajectory Remix MCP using synthetic tools, persistent synthetic subagents, invisible sandbox activity, correct timing and loss masks.
---

# Authoring parallel trajectories with MCP

Use this workflow to write a proposed training example. You are the **author** of
the entire scenario: the user, Kidra, the specialists, their replies and external
activity. Kidra is the **coordinator being trained**. Keep those roles separate.
Write each specialist's conversation yourself from its delegated task, retained
history and explicitly shared artifacts. No real agents, execution environment,
provider credentials or shell access are needed for this workflow.

Trajectory Remix is a simulator and editor. File tools modify virtual text in RAM, synthetic subagents consume authored outcomes, and replay reconstructs that state.
Nothing in a sample runs code, launches a worker or contacts a subagent provider.

## Connect and discover

1. Call `get_session` for the current revision, tokenizer, special token spellings,
   generation status and window-slot budget. Stop source editing while generation
   is running; do not guess tokenizer spellings or numeric window slots.
2. Call `get_authoring_guide` to read this file through MCP if you lack repository
   access. Call `list_simulated_tools` for the current synthetic argument schemas.
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
behavior. Separate logical ticks from the synthetic clock used by `time_now` and
`time_advance`.

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
manual result event. `delivery: "stream"` models the request being generated one
token per tick and starts execution after its final request token. The default
`delivery: "instant"` is a compact **authoring shortcut** that places the full
request at one tick; it should not be confused with the underlying one-token-per-
window generation model. The outcome remains an external observation in either
case. Specialist `conversationId` values track retained subagent conversations;
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

These are author-side `create_tool_call` arguments for one specialist turn:

```json
{
  "sample_id": "parallel-example",
  "tick": "10",
  "tool": "subagent",
  "window": "coder-call",
  "arguments": { "task": "Implement /math.py with double(value)." },
  "duration_ticks": 8,
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
  "tick": "20",
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

Overlap synthetic call execution intervals using timestamps and durations. For
example, start coder at tick 10 for 8 ticks and docs at tick 11 for 12 ticks, in
separate tool windows. At tick 12, `runtime_status` can report both conversations
running. A coordinator `chat` call at tick 13 can accurately describe that status.
At tick 20 the coder is available for the follow-up above, while docs is still
running. Schedule a reviewer after the required artifact becomes available.

`delivery: "instant"` (the default) places the full request at the requested tick;
`duration_ticks` starts there. This is useful for compact timing sketches.
With `delivery: "stream"`, Kidra writes one request token per tick. Execution
starts on the tick after the final request token; duration starts at dispatch,
not at the first request token. Inspect the returned `tool_call.start` and `end`
before scheduling dependent work. Use streamed requests for samples intended to
teach interleaving during request composition. Different windows can compose in
parallel; two emitting streams cannot occupy the same window at the same tick.

For custom streamed requests, `content` or `segments` controls the request text;
`tool` and `arguments` still determine the simulated operation. Keep them
consistent. To teach explicit waiting, add a segment such as
`{"text":"<|noop|><|noop|><|noop|>","loss":{"weight":1}}`, using the project's
configured NOOP spelling. Do not invent a wait/cancel/background-job
tool: those tools are not implemented. Status comes from scheduled conversations.

Ticks measure token scheduling, not real seconds. `time_now` and `time_advance`
use a separate synthetic clock. Automatic increments are per dispatched tool
call; an author-side inspection does not advance time. Write status messages
that match the observable scenario rather than claiming wall-clock runtimes.

## Publish external activity invisibly

For the coder's completion above, publish its file at tick 18 with:

```json
{
  "sample_id": "parallel-example",
  "tick": "18",
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

Allowed invisible tools are `file_write`, `file_patch`, `file_delete`,
`memory_save`, `note_save`, `note_delete`, `skill_save` and `time_advance`.
Do not attach `window`, `outcome`, `content`, `segments`, delivery or loss settings
to an invisible source event. Use `arguments.content` for a file body.
`duration_ticks` delays its commit; `source` attributes its external actor.
Hidden activity after all visible tokens extends inspection time without adding
idle training tokens. Prefer a later tick for dependent calls: equal-tick effects
follow stable source order, including numeric keys before anchor expressions.

Use `file_stat` / `get_sandbox_state` to obtain a version. Supply pinned references
to reviewers with `arguments.artifacts: [{"path":"/math.py","version":"sha256:..."}]`.
Only those versions and the specialist's retained history enter its context. Later
file edits do not rewrite an earlier review's context. Do not invent hashes or
claim a reviewer checked a newer version it was never supplied.

## Synthetic tool inventory

Always confirm current schemas with `list_simulated_tools`. All 22 tools are local
simulations; a stored skill is text, not installed code.

| Tools                                             | Required arguments and behavior                                                                                                                                    |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `chat`                                            | `content`; append a framed message from `metadata.agent_name` (default Kidra) to the open `chat` window.                                                           |
| `time_now`                                        | No arguments; read the synthetic timestamp.                                                                                                                        |
| `time_advance`                                    | Nonnegative `seconds`; advance synthetic time without sleeping.                                                                                                    |
| `file_write`                                      | `path`, `content`; save virtual UTF-8 text and return its version; optional `expected_version` prevents stale writes.                                              |
| `file_read`                                       | `path`; read current content, or historical content with optional `version`.                                                                                       |
| `file_stat`                                       | `path`; return current version and byte size without its body.                                                                                                     |
| `file_patch`                                      | `path`, `old`, `new`; replace exactly one nonempty unique occurrence; optional `expected_version`.                                                                 |
| `file_list`, `file_delete`                        | List virtual paths with no arguments; delete a current file with `path`. Historical versions remain.                                                               |
| `memory_save`, `memory_search`                    | Save `key`, `text`; search with `query`, returning up to eight lexical matches.                                                                                    |
| `note_save`                                       | `key`, `text`, `status` (`todo`, `doing`, `blocked`, `done`); optional `owner`, `depends_on`, `blockers`, `acceptance_criteria`. The last three are string arrays. |
| `note_list`, `note_delete`                        | List notes with no arguments; delete with `key`.                                                                                                                   |
| `skill_save`, `skill_load`, `skill_list`          | Save `name`, `content`; retrieve by `name`; list with no arguments.                                                                                                |
| `subagent`                                        | `task`; optional `resume` and `artifacts`; author supplies the outcome separately. No real delegation occurs.                                                      |
| `subagent_list`, `subagent_get`, `subagent_close` | List with no arguments; inspect/close with `conversationId`.                                                                                                       |
| `runtime_status`                                  | No arguments; inspect synthetic clock, call count, TODO counts and conversation lifecycle summaries.                                                               |

Optional note fields survive updates; clear them with empty strings/arrays. A
task cannot become `done` with blockers or missing/unfinished dependencies.
Self-dependencies and cycles are errors. Acceptance criteria are descriptive;
Remix does not execute them. Create a task per specialist and update statuses
when the corresponding synthetic observations justify it.

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

## Validate and hand off

1. Call `validate_trajectory` with its default `semantic: false`. This compiles
   locally without a provider. Repair diagnostics and inspect warnings. Optional
   semantic validators are separate configured model calls, not subagents.
2. Inspect `get_sandbox_state` and `preview_snapshots` before dispatch, while calls
   overlap, at completions, and after follow-ups. Running calls must not expose
   future answers/files. Prefer small ranges; snapshots are bounded to 100 frames
   and 4 MB, and interactive previews currently have a 50,000-row limit.
3. Use `get_window_history` to check request/output supervision, masked user/tool
   observations, NOOPs, window references and EOS closure. Source structure alone
   cannot prove a reviewer's claims are supported; compare its pinned context and
   authored answer yourself. Recheck dependent claims after editing an artifact.
4. Confirm TODOs, reviews, clarifications, progress messages and final handoff
   agree. Remove repetitive file bodies from visible text; use versions and short
   replies. Keep author instructions and raw specialist logs out of Kidra's loss.
5. Call `save_dataset`, then `get_trajectory` for source JSON to return to the
   requester. The UI can export source; the CLI can export training JSONL. Explain
   that this is a curated synthetic sample and report the checks actually done.

`examples/persistent-agents.json` is a small synthetic source example of these
mechanics. Extend the scenario's histories and timing
to suit the requested training behavior rather than copying generic replies.
