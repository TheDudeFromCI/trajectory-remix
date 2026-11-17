# Event generation and review

Remix can generate trajectories with models you configure, then review and repair them. This
page explains how a run works, what the reviewers do, and how failures are handled. For the
one-time setup, see [Project settings](PROJECT-SETTINGS.md). To check a local model, see
[Local-model validation](GRAPH-LOCAL-LLM.md).

Generation only calls the endpoints you've configured, and only when you start it. Models see
only what they need (see [What the model sees](#what-the-model-sees)), and synthetic tools are
never executed.

## Contents

- [Run a generation](#run-a-generation)
- [How a trajectory is built](#how-a-trajectory-is-built)
- [What the model sees](#what-the-model-sees)
- [Tools and chat during generation](#tools-and-chat-during-generation)
- [Reviews and repair](#reviews-and-repair)
- [When things go wrong](#when-things-go-wrong)
- [Reference](#reference)

## Run a generation

1. In **Settings**, add endpoints and assign the **Generator** role (and optionally
   **Fast** and **Critical** review roles). See [Project settings](PROJECT-SETTINGS.md).
2. Open **Sampler Matrix → Proposal preview → Generate trajectories**.
3. Choose any **pins**, a **seed** and an **attempt count**, then **Start generation**.

Each attempt uses `seed + ordinal`, so runs are reproducible. Pins are overrides for this
request only. They don't change weights or dirty the matrix. Save matrix or metric edits
**before** starting, because the run freezes its inputs at admission, and later edits to
settings or pins don't change its remaining attempts.

The job's page in the **Jobs** view shows pending, active and finished outcome counts, and manual
endpoint Resume controls. A run of N attempts reserves N identities and ends with N outcomes, including
failures. There are no automatic replacements.

## How a trajectory is built

The sampler picks a **proposal**. One adaptive Flow reads the entire proposal, including
custom matrix dimensions, and builds a bounded episode with branches, loops, tools,
authored specialist outcomes, verification and continuation as appropriate.

The private controller chooses one event envelope at a time. A separate content call
writes that event using its causal context. This separation keeps private objectives
out of the actor's prompt. New matrix fields reach the controller without a
competency-name switch in Python. The project tool catalogue still defines available
operations; matrix edits do not silently grant new tools. The controller can add new windows, independent branches
and joins; it cannot rewrite committed events or reopen closed windows. Structural
validation checks its choices before content generation. Targets guide construction;
a model's finish decision is not proof of semantic coverage or training readiness.

### The default flow

The packaged flow works in phases, much like the scenarios that came before it. Each
phase has its own step instruction under one shared selector instruction, and each
kind of event has its own writer prompt:

| Phase | Nodes | What happens |
| --- | --- | --- |
| Set the scene | `set_scene` → `choose_tools` → `write_opening` | Define the windows and the concrete world (persona, artifacts, a roster with observable capabilities, costs and context limits, budget, acceptance criteria, what the user has and has not authorized, hidden facts), choose and list the episode's tools (see [Show tools](#show-tools)), then write the opening request or trigger. |
| First move | `first_move` → `plan_is_thought` → `write_plan` | The agent's first step, normally reasoning that restates the goal and plans, delegating specialist work and weighing any automation choice. A direct reply, a clarifying question or an approval request takes the general route instead. |
| Work | `work` → routing → writer → `work` | One event at a time until the selector proposes to finish. The agent's own events are planning, delegation, review and communication; specialist output arrives as tool results or reports. |
| Verify | `wants_finish` → `verify` → `verified` → `done` | A proposed finish is audited against the proposal's objectives and the coordinator checks below. If anything is missing or violated, the verifier picks the event that fixes it and work continues. |

### Coordinator behavior in the default flow

Trajectory Remix authors data for a multi-agent coordinator, so the packaged flow treats
that role as the default standard. It never mentions voice or personality; that stays
opt-in.

| Topic | What the prompts ask for |
| --- | --- |
| Role | The agent plans, delegates, verifies and keeps continuity. When a suitable agent or tool is visible, it does not personally write production code, run extensive research or do bulk repetitive work. Delegation is proportional: a small job needs no team. |
| Strategic investment | When work recurs, demand is uncertain, or the proposal has an automation decision, the reasoning compares build cost, expected frequency, savings per use, maintenance, opportunity cost, reliability, uncertainty and reuse from visible evidence, then invests, defers or declines, and revisits the choice when new information changes the return. Neither automation nor manual delegation is preferred by default (a script suits 1,000 files, a cheap agent suits five). |
| Authorization | The scene defines what the user has and has not authorized. A consequential step outside that authority needs an approval request and a wait, not the action. |
| Prohibited patterns | Re-delegating an unchanged failed task, creating agents recursively or excessively, planning without moving to execution, and ignoring visible budgets. |

The authorization limits live in `task.world`, which only external authors see. The
agent learns them from visible evidence (the user's words, a policy document, a tool
contract), so the actor prompt stays generic. The `verify` step audits all of the above
and sends a violation back for a fix instead of passing it.

These defaults apply to projects with no saved flow. A project that already saved its
own flow keeps it; copy what you want from the packaged
[`flow.yaml`](../trajectory_remix/graph/flow.yaml) in the Flow tab. Tests use scripted
providers, so they show the structure is delivered, not that the prompts improve live
model output.

Routing is a chain of branches on `task.envelope.kind` (`is_tool_call`,
`is_tool_result`, `is_chat`, `is_external`) that sends each event to its writer:
`write_tool_call`, `write_tool_result`, `write_chat`, `write_external` or
`write_thought`. A writer's instruction is appended to the task, so it tells the event's
author how that kind of event should read. For actor events it follows the
`actor_instruction`, and it never carries private facts.

### Show tools

A `tools` node asks the Generator once which catalogue tools the episode needs. It sees
a compact index (tool ID and the schema's `description`), the proposal and the upcoming
opening event. Remix then writes the listing itself, never the model: a masked
`external_text` event in a window whose purpose is `tools`, with one line per tool
(`name`, `description`, `parameters`). Every tool the Generator did not choose is also
listed with the node's `extra_tools` chance (default 0.5), drawn from the trajectory and
event IDs, so the same trajectory always gets the same list and the agent learns to
ignore tools it does not need. Chosen tools are always listed.

The listing is placed before the next event (normally the opening), so every later event,
and so the agent, sees it in context. Afterwards selector requests carry only the listed
tools' schemas instead of the whole catalogue, and a tool call to an unlisted tool is
rejected with the list of tools that were shown. Before the listing, selectors see only
the compact index. Flows without a `tools` node send the whole catalogue as before.

Each selection, local transition, event and echo commits with its interpreter checkpoint.
Resume uses the frozen policy and accepted state. Loops have step and event limits;
exhaustion retains the partial source as **Unfinished**, without a fabricated pass.

### Edit the flow

The easiest way to edit the flow is the **Flow** tab in the dashboard: a flowchart you
can rearrange and connect, which saves the flow into the project database for you. Its **Walkthrough** mode
steps through one generation so you can see each prompt and reply. See
[Edit the flow chart](DASHBOARD.md#edit-the-flow-chart) and
[Walk through a generation step by step](DASHBOARD.md#walk-through-a-generation-step-by-step).

A project's flow is stored in its SQLite database, in the one-row `graph_flow` table,
as the YAML text you saved. With nothing saved, the packaged
[`flow.yaml`](../trajectory_remix/graph/flow.yaml) applies. The flow is read when a new
run starts; saving never changes active runs. A `flow.yaml` beside the database, from
older versions, is imported once the first time the flow is read and then ignored.
Alternatively pass YAML text as `instructions.flow_yaml` to override it for one run.
Invalid configuration is rejected before reserving attempts.
The complete normalized Flow, hash, proposal and tool catalogue freeze into the run.

The YAML declares `version: adaptive-flow/1`, `start`, `max_steps`, `max_events`,
`actor_instruction`, an optional `selector_instruction` and a `nodes` map. With
`selector_instruction` set, it is the system prompt of every `select` node and each
node's own `instruction` is sent as `current_step` after the data (see
[Prompt order and prefix caching](#prompt-order-and-prefix-caching)). Nodes support:

| Kind | Action and transitions |
| --- | --- |
| `select` | Ask the private authoring controller for the next event or a finish decision; continue at `next`. |
| `event` | Generate the selected event's content; continue at `next`. |
| `branch` | Compare dotted `path` in the last selection with `equals`; take `then` or `otherwise`. Missing paths take `otherwise`; booleans are distinct from integers. |
| `end` | Finish construction after a validated finish selection, then run configured reviews. |
| `tools` | Show tools: ask which tools the episode needs, from tool IDs and descriptions, then list them for the agent; continue at `next`. See [Show tools](#show-tools). |

Transitions can point backward to loop or forward to join branches. Step limits bound
all cycles, including cycles without provider calls. Edit node instructions and add
conditional branches to extend behavior. The last selection exposes `finished`,
`reason`, `task` and `windows` to conditions. Controller instructions are trusted author
configuration; generated actor instructions are replaced with `actor_instruction` to
prevent private solution leakage. External authors may receive scoped `task.world`.

The default keeps decision windows open through their consumers. Closing a window
removes it from later actor prompts; choose closure only after its final use.

### Custom recipes

Advanced users can replace the default with a frozen recipe in the run instructions:
`{"plan": {"windows": {...}, "tasks": [...]}}`. A recipe is a branch of the same driver, not a
separate engine. Each task has:

| Field | Meaning |
| --- | --- |
| `id` | Stable event ID. Don't end it in `:echo`, which is reserved. |
| `envelope` | Everything except `content`: kind, window, dependencies, masks, delivery, framing, closing. Dependencies point at earlier selected tasks. |
| `instruction` | The authoring task for this event. Actor tasks must not contain privileged answers, future facts, expected verdicts or private summaries. |
| `world` | Private facts for **external** authors only. Rejected on actor tasks. Omit anything not needed. |
| `when` | Optional dotted proposal paths and equality conditions. Non-matching tasks are skipped. Broken dependencies are rejected, not rewired. |
| `arguments` | `text` (default) returns `{"content": "…"}`. `json` returns `{"arguments": …}` for a complete known call. Use text for fragmented or intentionally invalid calls. |

Instructions are trusted project configuration and must respect the same boundaries. The smoke
script's `smoke_plan` has examples.

## What the model sees

The private selection controller sees the full proposal and committed source. It is an
authoring role, never supervised actor output. For each content event, the actor prompt contains only **causal ancestors**, folded into the windows that are
currently visible, with replacements applied and closed windows removed. Parallel and future
events, metadata, the full proposal, hidden world data and review history are all excluded. There
is no rolling chat history that could smuggle lost context back in.

## Tools and chat during generation

- Tool calls and results are written like any other event. Results are free-form, with
  independent begin/end, instant or streamed delivery, and closing flags.
- A completed `chat` call with `echo_chat=true` produces a masked `chat_message` from the call's
  parsable `content`, linked by `call_id`. A pending-helper checkpoint makes restarts safe.
  Legacy explicit-plan tasks resolve call dependencies through its echo; adaptive
  selections refer to the committed echo ID when delivery must precede an action.
  A linked chat echo that differs from submitted content is a structural error,
  including after repair.
- With `echo_chat=false`, the Flow lets you filter, reject or skip delivery through explicit tasks.
- Malformed chat arguments only warn, and no echo is invented. Multiple fragments assemble in
  causal order.

## Reviews and repair

Two optional reviewers check a finished trajectory against editable checklists:

- **Fast** covers the **Core** checklist.
- **Critical** covers **Core + Critical**.

Each criterion needs a verdict, **nonempty evidence** and valid event references. Missing or
unknown items, invalid JSON, invented event references and unsupported not-applicable answers
never produce a verdict. A pass is blocked while structural errors remain, though warnings don't
block it. Sign-offs record the exact checklist, source revision and hash, tier, model and
endpoint, prompt version, evidence and resulting stage change.

### Stage transitions

| Event | Result |
| --- | --- |
| Generation completes | Unfinished → **Pending (0)** |
| Fast review passes / fails | **Candidate (1)** / **Unsuccessful (-1)** |
| Critical review (after Fast pass) passes / fails | **Validated (2)** / **Rejected (-2)** |
| You approve | **Ready (3)**, always manual |

If a review role is missing, generation still works with the Generator alone. Critical never
skips Fast. Configured roles are used automatically unless the project toggle turns them off.

### Repair

A failed review can enter **one** Repair & Validate session on that tier's own model. The model
sees the original source and checklist, plus each full inspect-and-patch exchange so far, until a
verdict or the context budget (`repair_context_bytes`, default 65,536) runs out. There is no
compaction, second attempt or extra confirmation pass.

- *Inspect* returns a causal view of events. A *patch* merges into one event or window using the
  expected source hash, and an event can be added or removed. Removal keeps broken references for
  you to fix.
- Accepted patches save immediately, even if they leave editable errors. A passing final verdict
  needs a structurally valid graph.
- On failure or context exhaustion, the trajectory is detached as Unfinished with its edits
  and any real verdict evidence kept. No verdict is made up for a context-limit failure.
- A successful Fast repair can proceed to Critical review.

### Manual review

**Review** and **Review & Repair** run from the trajectory header's **More** menu on the selected saved
revision, or from the dataset's bulk actions on every match of a search across all pages.

The dashboard always uses Standard mode. Force modes are available through the REST API and MCP.

| Mode | Behavior |
| --- | --- |
| **Standard** | Pending gets Fast only. Candidate gets Critical. Failed stages repair at their own tier if repair is on. Everything else is skipped. |
| **Force Fast / Critical** | Ignores stage limits. A pass keeps a higher existing stage, and failures follow normal transitions. |

Ownership is always enforced, and a locked source is skipped. Closing the dialog doesn't stop the
job; follow it in the **Jobs** view.

### Stale reviews

Changing a source or checklist makes older sign-offs **stale** without changing stages or rewriting
history. Core changes affect Fast and Critical, and Critical-only changes affect Critical. Current
reads show `review_status`, and export's "exclude stale" option uses the same hashes. Nothing is
re-reviewed automatically. A bulk review leaves one Undo/Redo step that restores saved results
without calling a model.

## When things go wrong

- **Transport errors** (after the endpoint's retries): that endpoint **pauses**. Stages and
  attached work are kept. Fix the server and use **Resume endpoint**.
- **Format failures** (the model returned unusable JSON): a bounded number of new calls
  (`format_retries`, default 2 extra). Generation that runs out records **Unsuccessful** and may
  go to repair. For review and repair, the job **pauses with no quality verdict**, and Resume retries
  within the same session and budget.
- **Local wrapper problems** pause the job instead of asking a model to fix programmatic envelopes.
- **Server restart:** jobs pause, callbacks are fenced, unaccepted text is dropped, and you
  resume manually. Completed events and repair patches survive.
- **Cancel and detach** use the same fences as everywhere else ([Jobs](GRAPH-JOBS.md)).

## Reference

**Settings** (`generation` in project settings): `fast`, `critical`, `repair` (default true),
`format_retries` (default 2) and `repair_context_bytes` (default 65,536, a transcript bound, not
a token estimate). Configure the `chat` tool with a JSON object input that has a string `content`
to enable the echo helper ([example config](../examples/graph_local_llm.json)).

**Endpoints:** URLs are OpenAI-compatible base URLs. Remix appends `/chat/completions`, sends a
non-streamed structured-output request, and uses no SDK retries or redirects. Credentials come from
`REMIX_API_KEY_<ENDPOINT>` in the environment.

**Output formats.** Simple closed objects use strict native JSON-schema decoding. Repair root unions
and unrestricted tool-argument objects aren't in the common strict subset, so those requests use
JSON-object mode with the full schema in the prompt. The graph schema is still validated locally, with
the same retry policy. Your local server must support the requested JSON response format.

**REST routes:**

| Route | Action |
| --- | --- |
| `POST /api/v1/generation/runs` | `{requests:[{seed, overrides, …}], instructions:{…}}`. Needs a Generator. Returns a job. |
| `POST /api/v1/sampler/runs` | The same driver when a Generator exists, otherwise a paused snapshot-only run. |
| `POST /api/v1/reviews` | `{query, mode, repair}` or `{trajectory, expected_revision, mode, repair}`. |
| `GET /api/v1/trajectories/{id}` | Source, ownership, diagnostics and review staleness. |
| `/jobs`, `/attempts`, `/results`, `/history` and controls | Progress, skipped targets, saved deltas, Pause, Resume, Cancel and detach. |

MCP offers `generate_graphs` and `review_graphs` over the same services. Generic runs without this
driver's frozen recipe stay paused.

**For developers.** `GenerationService(..., transport)` takes an async single-invocation transport.
It receives a queue `Ticket`, sends only `payload.messages` and `schema` using the pinned endpoint
config, and returns `{output, model, usage?}`, `{invalid_output: true}` or `{context_limit: true}`.
Each event, review and repair turn is a separate FIFO item, and its original payload is kept for retry
while response bodies are dropped on interruption. Provider usage is evidence only, not a source metric.
Checks: `python -m pytest -q tests/test_graph_generation.py tests/test_graph_jobs.py
tests/test_graph_sampling.py tests/test_graph_studio.py tests/test_graph_export.py` and
`python tests/run_generation_frontend.py`. They use tiny fake-provider sources. Model quality,
tool coherence, schema compatibility and context limits need the user-machine checks.

### Backend API changes: adaptive Flow

Frontend integration can continue using `POST /api/v1/generation/runs` with the same
request body. Empty `instructions` now selects the adaptive YAML Flow. Existing
`instructions.plan` recipes and already-frozen runs retain their original behavior.
Do not send both `plan` and `flow_yaml`.

New authenticated `GET /api/v1/generation/flow` returns `{flow, flow_hash}` for the
project's saved policy (or packaged default). Editing is through the Flow
editor's save route (see [Flow editor and walkthrough API](#flow-editor-and-walkthrough-api))
or a per-run `instructions.flow_yaml` string.
MCP `generate_graphs` accepts the same instructions through its existing service.
There are no source-format, settings-schema, stage or ownership changes.

Provider request history now includes `operation: flow_select`, separate from
`event`, `review` and `repair`. It uses the Generator role and ordinary FIFO limits.
The checkpoint has an additional `flow` object with node, steps, output and task;
consumers should treat it as opaque. Job logs report successful selections and limit
exhaustion without copying private prompts or response bodies. `flow_outcome` metadata
records the controller's finish explanation; it is not measured coverage. Compilation
and semantic acceptance remain separate.

### Flow editor and walkthrough API

These routes back the dashboard's **Flow** tab. They need the same local session token
as the rest of `/api/v1`.

| Route | Action |
| --- | --- |
| `GET /generation/flow` | `{flow, flow_hash}` as before, plus `text` (the saved or packaged YAML), `origin` (`project` or `packaged`), `revision`, `valid` and `diagnostics`. |
| `POST /generation/flow/check` | `{flow}` or `{yaml}`. Returns `valid`, `diagnostics` (each with `severity`, `code`, `message`, and `node`/`field` when it has a location), the normalized `flow`, `flow_hash` and rendered `yaml`. Never raises for a bad draft. |
| `PUT /generation/flow` | `{flow}` or `{yaml}` with `expected_revision` from the last read. Stores the flow in the project database in one transaction. `yaml` text is written verbatim; `flow` is rendered with only the fields each node kind uses. A stale revision returns 409 `flow_conflict` with `current_revision`; errors return 422 and leave the saved flow untouched. |
| `POST /walkthrough/start` | `{seed, overrides, flow?, flow_yaml?}`. Samples one proposal (not stored) and freezes a plan exactly as a run does. Returns `{state, next}`. |
| `POST /walkthrough/step` | `{state, mode: "call" \| "manual", response?, latest_settings?, call_id?}`. Runs one transition and returns `{state, trace, next}`. `latest_settings: true` uses the latest saved project settings instead of the walkthrough's own revision. |
| `POST /walkthrough/describe` | `{...state}`, optional `?latest_settings=true`. Returns `next` for a state without running it, with the `settings_revision` it used. |
| `POST /walkthrough/cancel` | `{call_id}`. Stops that step's waiting model call; the step returns with its state unchanged and `cancelled: true` is returned here. |

Walkthrough calls (never job calls) also return the start of an unusable reply's text as
`raw_content` and `raw_reasoning` in the trace's `response`, so a cut-off reply can be read.

`next` describes the coming step: `action` (`model`, `branch`, `chat_echo`, `end`,
`limit`, `blocked` or `done`), the node, a summary, `knows` (the interpreter's position,
memory and draft diagnostics) and, for model steps, `request`: the payload messages,
schema, task, endpoint, the exact HTTP body without credentials (`wire`) and the context
budget. `trace` adds what happened: `accepted`, `error`, `response` (model, usage and
output), the branch value read, the accepted Selection or written event, `added_events`
and `to`.

Walkthroughs use the same prompt builders and acceptance checks as runs
(`adaptive_driver.request_payload`, `accept_selection`, `append_event`), so a step
shows what a run would send and accept. A rejected reply keeps the state and records the
correction; passing `format_retries` ends the walkthrough as `invalid_provider_output`,
as a run would mark it Unsuccessful. The state lives in the browser; nothing is written
to the dataset or job tables, and the proposal snapshot is not persisted. `call` mode
sends one request straight to the Generator endpoint, at most one walkthrough call at a
time, outside the job FIFO and without its retries. It is an explicit, interactive
action; walkthrough errors are returned to the caller and never logged.

### Context scope and local-model budgets

The controller receives the **sampled proposal**, not the entire sampling matrix.
Source metadata (including its frozen matrix, provenance snapshots and review history)
is retained in storage but excluded from controller prompts. Reviewers receive each
source body once; their per-decision causal views list surviving event IDs by window,
including replacement and closure effects. These are references to the source, not
summaries that can introduce new facts.

Endpoint settings now accept `context_window_tokens` (default **16384**) and
`max_output_tokens` (default **2048**). The transport sends the latter as `max_tokens`.
Configure these through endpoint JSON until frontend forms expose them. This is an
additive endpoint-settings API change; the request and source schemas are unchanged.

Before HTTP, the transport measures the serialized messages and response schema in
UTF-8 bytes and reserves output tokens plus 512 framing tokens. It estimates input tokens
as bytes divided by the endpoint's `bytes_per_token` (default **1**, which can never
undercount for byte-backed tokenizers but rejects many requests that would fit). English
and compact JSON average about 3.5 bytes per token; **3** keeps a margin while using most
of the real window. See [Provider options](PROJECT-SETTINGS.md#provider-options).
It is not an exact Qwen chat-template count. No training tokenizer is loaded and no
model endpoint is probed. Configure the actual model context size, rather than inflating
it to bypass this check. Exact serving-tokenizer accounting is a future extension.

#### Prompt order and prefix caching

Requests are laid out so that an inference server with prefix (KV) caching can reuse as
much of the previous request as possible. Caching saves prompt-processing time and
cost; it does **not** enlarge the context window, because cached tokens still count
toward `context_window_tokens`.

- **Selector requests:** the flow's shared `selector_instruction` as the system prompt
  (the same for every selector node and every trajectory), then one user message. In
  JSON mode it starts with the fixed output schema, followed by the data from most to
  least stable: `tools`, `proposal`, `source`, `current_step` (this node's ID and
  instruction), `remaining_events`, `previous`, `correction`. Inside `source`, events are
  listed in causal order, so a newly committed event is usually appended at the end.
- **Event requests:** the fixed system prompt, then `tool`, `input_schema`, `kind`,
  `task`, `window_purpose`, `visible`, `world` and `correction`.

`current_step` deliberately comes after the source, so the instruction sits next to the
answer, which small open-weight models follow more reliably. Moving it earlier would
save reprocessing only the step text (a few hundred tokens) per call, at the cost of
burying it behind the whole trajectory. A flow without `selector_instruction` keeps the
older layout: each node's instruction is its system prompt and there is no
`current_step`.

The roles always alternate (system, then user): the JSON-mode schema is joined to the
user message rather than sent separately, because some open-weight chat templates
reject two user messages in a row. Keep this order when changing prompt builders;
`tests/test_prompt_cache_order.py` checks it, and the walkthrough's **Prompt** tab
shows the exact text sent.

#### What is left out of prompts

Prompts carry what the model needs and nothing that is implied:

- **Default-valued fields are dropped** from events and windows, in both the selector's
  source and the writers' visible context. An event shows its kind, window, dependencies,
  content and anything set to a non-default value; null call IDs, false flags and default
  delivery and supervision settings are omitted. At 100 events this saves roughly 20 KB.
- **`previous` holds the selector's own notes**, `finished`, `reason` and the chosen
  task's ID, not the full last Selection: the task envelope is already in the source.
- **Writers see only causal ancestors** of the event they write. Parallel branches and
  later events are never included; `tests/test_prompt_scope.py` checks this. Only the
  private selector sees the whole committed source, all of which is in the past.

Over-budget requests return a context-limit result before contacting the model. A
non-repair operation pauses with operation/input-size/allowance in its job log; repair
retains the existing context-limit failure behavior. Nothing is silently truncated or
converted into a quality failure. Frozen runs keep their endpoint settings: start a
new run to apply changed budgets or the narrower prompt recipe. Long episodes still
need explicit context management; this change removes accidental duplication rather
than claiming unlimited history fits a small model.

### Watch a running job

The existing job log shows timestamped admission, request queue/start, response,
event commit, review verdict and trajectory outcome messages. Requests that are
still pending a complete model response emit an elapsed-time heartbeat every ten
seconds. A heartbeat means the request is still awaiting a response; it does not
claim tokens are being generated. Queue messages identify paused endpoints, and
retry messages distinguish another attempt from exhausted retries requiring Resume.
Responses report provider token counts when supplied, and input sizes are bytes,
not estimated token counts.

Logs contain operation, endpoint and trajectory/request/event IDs, timings and
counts. They do not include prompts, model output, provider error bodies or keys.
The transport still receives whole responses rather than streaming tokens. The
existing log endpoint and job dialog need no API changes; polling an open job's
log shows new entries even while the model is busy. Existing log-cache retention
and Clear Log Cache apply; a zero-size cache retains no entries.

### Endpoint sampling and timeout controls

Add these fields to `settings.endpoints[id]` through the existing settings GET/PUT
API. The settings JSON Schema exposes them at `GET /api/v1/schema/settings`.

| Field | Default | Valid values |
| --- | --- | --- |
| `temperature` | `null` | Number from 0 to 2, or `null` |
| `top_p` | `null` | Number from 0 to 1, or `null` |
| `presence_penalty` | `null` | Number from -2 to 2, or `null` |
| `timeout_seconds` | `120` | Finite number greater than zero |

A blank sampling control should save as `null`: the transport omits it so the
provider/model chooses its default. Zero is an explicit setting and is sent.
Use the canonical `temperature` field, not `temp`. These are standard
[OpenAI Chat Completions parameters](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create);
some individual models restrict them. Leave unsupported controls blank rather
than relying on automatic detection or a hidden retry with different settings.
`top_k`, `min_p` and `rep_penalty` are not exposed because they are not standard
OpenAI Chat Completions request parameters. Repetition penalty is not mapped to
frequency penalty.

Timeout is a client setting, never part of the JSON sent to the model. It bounds
each complete request attempt and individual network operations. Timeouts follow
the existing endpoint retry/exhaustion behavior; each retry gets a fresh deadline.
It does not limit the whole generation job. All four fields freeze into new run
settings and apply to selection, content, review and repair requests using that
endpoint. Edits do not alter an active run. No frontend files or routes changed.
