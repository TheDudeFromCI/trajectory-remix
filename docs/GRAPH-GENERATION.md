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
- [Draft and Assembly](#draft-and-assembly)
- [Long-trajectory experiment](#long-trajectory-experiment)
- [Edit the prompts](#edit-the-prompts)
- [What the model sees](#what-the-model-sees)
- [Tools and chat during generation](#tools-and-chat-during-generation)
- [Reviews and repair](#reviews-and-repair)
- [When things go wrong](#when-things-go-wrong)
- [Reference](#reference)

## Run a generation

1. In **Settings**, add endpoints and assign the **Draft** role, and
   optionally **Assembly**, **Fast Review** and **Critical Review**. See
   [Project settings](PROJECT-SETTINGS.md).
2. Open **Sampler Matrix → Proposal preview → Generate trajectories**.
3. Choose any **pins**, a **seed** and an **attempt count**, then **Start generation**.

Each attempt uses `seed + ordinal`, so runs are reproducible. Pins are overrides for this
request only. They don't change weights or dirty the matrix. Save matrix or metric edits
**before** starting, because the run freezes its inputs at admission, and later edits to
settings or pins don't change its remaining attempts.

The job's page in the **Jobs** view shows pending, active and finished outcome counts, and manual
endpoint Resume controls. A run of N attempts reserves N identities and ends with N outcomes, including
failures. There are no automatic replacements.

The generation progress bar counts completed stages. Five trajectories with Draft, Assembly,
Fast and Critical enabled have 20 planned stages. Accepted results advance the count once per
stage. Skipped review roles are excluded, and early failures leave the uncompleted stages
visible in the total. The attempt counts still show how many trajectories settled.

## How a trajectory is built

The sampler picks a **proposal**. Each trajectory then goes through four stages:

**Draft → Assembly → Fast Review → Critical Review**

Draft writes the whole episode as prose. Assembly turns it into a graph and fixes its
structure. The two reviews check it against the checklists and repair what fails. The whole
proposal reaches Draft and Assembly, including custom matrix dimensions, so new matrix fields
need no code changes. The project tool catalogue still defines the available tools; matrix
edits don't grant new ones. Targets guide the episode, but a passing review isn't proof of
semantic coverage or training readiness.

## Draft and Assembly

Draft writes the working notes in one response. Assembly turns those notes into a graph
over one continuing tool conversation, then hands the signed-off graph to review.

| Stage | Endpoint role | What happens |
|---|---|---|
| Draft | **Draft** | Plain-prose working notes for the whole episode: messages, reasoning, tool calls and their results, parallel work, and what the agent can confirm at the end. |
| Assembly | **Assembly** (falls back to Draft) | Uses native tools to create and inspect the graph, resolve diagnostics and sign off on completeness and Draft fidelity. |
| Fast Review | **Fast Review** | Checklist review that repairs what fails, then signs off. |
| Critical Review | **Critical Review** | Stricter checklist review that repairs what fails, then signs off. |

Draft and Assembly can share one endpoint, or you can give Assembly a stronger model for better
structure. The Draft and Assembly calls see the proposal, the tool catalogue, the review checklist
(core and critical) and the project's prompts (see [Edit the prompts](#edit-the-prompts)).

### Assembly

The Assembly endpoint must support tool calls in Chat Completions. Remix presents native tools
for editing events and windows, inspecting their causal dependencies, writing chat and posting
tool schemas. A reply can request several tools. Remix applies them in reply order and saves the
result and conversation together. An edit can leave a working draft with diagnostics.

Use `view_diagnostics` in a tool batch to check the graph after preceding calls and before
later edits. Remix returns that snapshot with the batch's other tool results on the next
model turn.

Assembly is complete when `signoff` accepts evidence for completeness and fidelity to the
Draft. Resolve all errors and enabled warnings first. A regular text reply leaves the same
conversation open. The Draft and signoff stay with the trajectory in `metadata.draft` and
`metadata.authoring_signoffs`. Jobs started before review and repair became one step pause with a
message; start a new run or review instead.

## Long-trajectory experiment

Turn on **Enable long-trajectory experiment** in **Review Settings** and save before
starting a generation job. The experiment uses the task length already selected by the
sampler. XXS through S aim for about 32,500 content tokens and 2,000 supervised agent
tokens. M aims for about 65,000 and 4,000. L through XXL aim for about 130,000 and 8,000.
Projects using older matrices derive the same three bands from the combined complexity
axis. These guide the models. Actual lengths can vary.

Draft creates a small outline from the endpoint's text reply, then writes one prose
section per call with a short handoff for the next section. Assembly checkpoints
each section into the same graph and opens a fresh conversation for the next section.
Reviews inspect and repair one section at a time, then check the combined graph at
signoff. Reads can fetch exact event text, causal evidence and Draft sections in pages.
The Jobs log shows section checkpoints and conversation restarts. The stage progress
bar advances when the entire stage completes.

**Approximate tokens per section** controls the size of the planned sections. The default
is 4,000. The endpoint's context and output limits bound each request. When the limits are
unset, the experiment uses a 32,768-token context and reserves up to 8,192 output tokens.
If a conversation runs out of context or exchanges, a fresh conversation can resume the
section from the saved graph. Repeated failures end the pass with the accepted edits saved.

Character-based estimates appear in the full source export's scaling metadata. Compiled
exports add exact body counts using the export tokenizer, supervised agent tokens, and
compiled row and peak visibility measurements to the manifest. Check the realized
lengths and loader footprint before using the resulting data for training.

### Generate and review in separate sessions

If you can't load your generation and review models at the same time (for example two local
models that don't fit in VRAM together), use the **Phase barriers** checkboxes in
**Generate trajectories**. They are chosen per job, so each run can pause in different places.

| Checkbox | The job waits until every trajectory has... | Resume starts |
|---|---|---|
| Pause before Assembly | finished Draft | Assembly |
| Pause before Fast review | been assembled | Fast review |
| Pause before Critical review | reached Critical review | Critical review |

With a barrier on, a trajectory that reaches it **parks** instead of going on. When every
trajectory is parked, held or finished, the job **pauses** and the log says so. With several
barriers on, the job pauses at each in turn. All three are off by default, and the choice is
frozen with the job (the dialog remembers your last choice).

Parked trajectories don't count toward the active-trajectory cap, so a run of 100 drafts all 100,
not just the first 20. The barrier state is stored with the job, so it survives shutting the
server down between sessions. Over the API, send `barriers` with the run:
`{"requests": [...], "barriers": {"assembly": false, "fast": true, "critical": true}}`.

A typical day-and-night cycle with two local models:

1. Give **Draft** and **Assembly** an endpoint for the generation model, **Fast Review** an
   endpoint for the review model on a different server or port, and **Critical Review** a hosted
   endpoint. Tick **Pause before Fast review**.
2. Load the generation model and start a run. Draft and Assembly run; Fast review waits.
3. When the job pauses at the barrier, check the log and any trajectories held for attention.
   You can shut everything down now.
4. Next session: start the server, load the review model, open the job and click **Resume**.
   Reviews run until the job completes.

If generation hasn't finished when you want to switch, click **Skip barriers** on the job
(`POST /api/v1/jobs/{id}/phase/review`). Waiting trajectories go on; unfinished ones carry
on once their endpoint is back. An endpoint whose server is offline pauses
itself after its retries; resume it from the job page when the model is loaded.

Point the two local endpoints at **different servers or ports**. If both use one server that
answers with whatever model is loaded, review requests can silently go to the generation model.

### Edit the prompts

The prompts are project settings, under **Review Settings → Generation
prompts**:

| Prompt | Used by | What it's for |
|---|---|---|
| **Agent prompt** | Every endpoint: Draft, Assembly, Fast and Critical Review, Assistant | What a trajectory is (windows, events, chat, tools, parallel subagents, the window lifecycle and ticks) and how the agent behaves. Add a persona pack at the end. |
| **Authoring prompt** | Assembly, Fast and Critical Review, Assistant | How to use the authoring tools, like a small skill file. |
| **Draft instructions** | Draft | The Draft task: how to write the working notes. |
| **Assembly instructions** | Assembly | The Assembly task: how to turn the notes into a graph. |
| **Assistant instructions** | Assistant | The Assistant task, placed before the comments table, including how to answer with `reply`. |

**Load Default Prompts** restores the packaged ones (`graph/draft_assembly.yaml`) after asking
you to confirm; nothing is saved until you choose **Save settings**. Runs freeze the prompts
when they start, and each request records a prompt version: the packaged version, plus a short
hash when you've edited them.

A project still using an earlier shipped prompt gets the new one when it opens. An agent prompt
that starts with the earlier default, for example with a persona appended, keeps the appended
text after the new default. Prompts you wrote yourself are never replaced.

Projects from before Draft and Assembly kept their prompts in the Flow. When such a project
opens, a customised Flow agent prompt (for example one with a persona pack appended) moves into
**Agent prompt** once, and the old Flow is then discarded.

## What the model sees

Every system prompt starts with the **Agent prompt**. The endpoints that use the authoring
tools add the **Authoring prompt** after it. The task and its data go in the first user message:

| Endpoint | System prompt | First user message |
|---|---|---|
| **Draft** | Agent prompt | Draft instructions, then the sampled proposal and the project tools as JSON. |
| **Assembly** | Agent and authoring prompts | Assembly instructions, the trajectory ID, the project tools and the Draft. |
| **Fast / Critical Review** | Agent and authoring prompts | A request to review the trajectory against the checklist, repair any issues and call `signoff`; the review checklist, the objective and sampled targets, the project tools, and the trajectory ID. |
| **Assistant** | Agent and authoring prompts | Assistant instructions (which ask for a `reply` to each comment), then the open comments as a table with their IDs and threads, and the trajectory ID. A follow-up send starts with a note that the trajectory may have changed. |

- Sessions with tools start from the trajectory ID and read the graph with `view_graph`,
  `view_diagnostics`, `view_event` and `view_window`. The criteria are also in the `signoff`
  tool's parameters.
- In Review Settings, turn on **Include Draft in review** to show the Draft to
  reviewers and require Draft-fidelity evidence whenever the source has a Draft. It defaults to off.

Synthetic tool outputs are authored event content. Remix performs no tool execution.

## Tools and chat during generation

- `post_tool_schema` replaces the initial masked `tool_schema` event with the project tool IDs
  you supply. Creating or editing a call to a known tool adds its schema if needed. Calls
  depend on the initial listing directly or through their other prerequisites.
- Author results as `tool_response` events. Use the same call ID and depend on the event that
  submits the call.
- Give each call its own tool window. Author its response there when the tool configuration
  allows it, then close the window once its last consumer has used the result. Keep reasoning
  in a separate window. The initial tools window opens with an empty dependency list.
- `post_chat_message` writes a masked user message or creates an agent's synthetic `chat`
  request in a short-lived window, followed by a matching delivery. The supplied event ID
  identifies the delivery.

## Reviews and repair

Two optional reviewers check a finished trajectory against editable checklists:

- **Fast** covers the **Core** checklist.
- **Critical** covers **Core + Critical**.

Each review is one step: a native tool conversation on that tier's endpoint, with the same
authoring tools Assembly uses. The reviewer reads the trajectory, repairs anything that fails
a criterion, and signs off. Each accepted exchange saves its edits and conversation.

Use `signoff` to record evidence and valid event IDs for every requested criterion. A passing
signoff requires zero errors and zero enabled warnings. Configure warning opt-outs under
**Project Settings → Validation**. Signoffs preserve the source and settings identity,
checklist, tier, model, endpoint and transition.

A pass on the Core causal-evidence criterion includes `causal_claims` with a consumer
event and the source event IDs it uses. Signoff checks each claim against the consumer's
visible windows and requires closure or replacement to follow the consumer. Tool responses,
documents, or prior reasoning can supply evidence. The dry run also warns about a direct
tool result dependency that was closed or replaced before agent output.

### Stage transitions

| Event | Result |
| --- | --- |
| Generation completes | Unfinished → **Pending (0)** |
| Fast review passes / fails | **Candidate (1)** / **Unsuccessful (-1)** |
| Critical review passes / fails | **Validated (2)** / **Rejected (-2)** |
| You approve | **Ready (3)**, always manual |

Automatic review skips unassigned or disabled roles. If only Critical is enabled and assigned,
it starts directly after Assembly and covers Core + Critical. When both steps run, Critical waits
for a Fast pass. With both roles skipped, successful Assembly leaves the trajectory Pending.

### How a review ends

| Ending | Result |
| --- | --- |
| `signoff` passes every criterion | The review passes and the stage moves up. An automatic Fast pass goes on to Critical review. |
| `signoff` with a failing criterion | Refused. The reviewer keeps fixing in the same conversation. |
| `signoff` with `status: "unfeasible"` and a reason | The review fails: the stage becomes Unsuccessful or Rejected, and the evaluation records `verdict: "fail"`, `unfeasible: true` and the reason. Edits are kept. |
| **Maximum exchanges per pass** or the endpoint context limit | No verdict. The stage stays, edits are kept, and the outcome is in `metadata.review_outcomes`. The default is ten exchanges; a reply with several tool calls counts once. If the same endpoint hits the exchange limit twice in a job, Jobs logs a warning with the setting to adjust. |
| Replies that stay malformed | The trajectory is held for attention, with no verdict. |

### Manual review

**Review** runs from the trajectory header's **More** menu on the selected saved revision, or from
the dataset's bulk actions on every match of a search across all pages. It reviews, repairs what
fails and signs off.

The dashboard always uses Standard mode. Force modes are available through the REST API and MCP.

| Mode | Behavior |
| --- | --- |
| **Standard** | Pending uses the first assigned review role. Candidate gets Critical. Unsuccessful gets Fast and Rejected gets Critical again. Everything else is skipped. |
| **Force Fast / Critical** | Ignores stage limits. A pass keeps a higher existing stage, and failures follow normal transitions. |

Ownership is always enforced, and a locked source is skipped. Closing the dialog doesn't stop the
job; follow it in the **Jobs** view.

### Assistant sessions

**Send open comments to agent** (the editor's Comments tab, or `POST /api/v1/trajectories/{id}/assistant` with
`expected_revision`) runs one trajectory through the **Assistant** endpoint role. It's a one-item job like a selected
review, and it needs the saved revision you're looking at.

| Step | What happens |
| --- | --- |
| Admission | The open comments and their replies are frozen into the job. Comments added later wait for the next send. |
| Session | A native tool conversation. The model gets the comments table and the trajectory ID, reads and edits the graph with the authoring tools, and answers with `reply`. Tools act on a copy-on-write working copy: the pending proposal if there is one, otherwise the trajectory as it was at admission. The trajectory itself is never edited. |
| Metrics | `view_metrics` returns every metric definition with its value, origin and status (available, stale or unavailable). `set_metric` records an annotation-scope value, or clears it with `null`, on the working copy; it's checked like a manual annotation and proposed with the session's edits, with origin `assistant_annotation`. Calculated (`source`) and `export` metrics are read-only. A later graph edit makes the value stale. |
| Replies | `reply` answers one comment, once. `done: true` adds the note as an agent reply and marks the comment addressed, for the user to accept or send back; `false` adds the note as a reply and leaves it open. The model never resolves a comment. |
| Finish | The session ends when every frozen comment has a reply; the replies are posted then. A reply without tool calls while comments remain gets a reminder listing them. If the working copy differs from its base, it is kept as the trajectory's proposal; otherwise any proposal is dropped. |
| Next send | The finished conversation is kept. The next send appends the new comments to it as a user message. When the local context check is enabled and the conversation exceeds the Assistant history budget (`assistant_context_bytes`), the new job starts afresh. |
| Limits | Invalid replies get the usual bounded corrections. Reaching **Maximum exchanges per pass** or a context limit ends the job with its edits kept as the proposal and unanswered comments open. A context-limit stop also drops the kept conversation. Set the endpoint's **Context limit** to `-1` to leave context enforcement to the provider, including for Assistant history. |

The stage and review history aren't touched; applied edits make an earlier review stale as usual.

#### Proposals

A proposal is stored outside the source, with the revision it was built from (its base) and the comments it addressed.
Admission rebases a pending proposal onto the current revision first, so a follow-up session sees edits made since;
both sides changing the same entity refuses the send with `proposal_conflict` (409). Entities are the defaults, each
window, each event and each metadata key.

| Route | What it does |
| --- | --- |
| `GET /api/v1/trajectories/{id}/assistant/proposal` | `{proposal: null}`, or the base revision, the comment IDs, `changes` (each `{kind, id, change, before, after}`, `change` being `added`, `removed` or `changed`) and `conflicts` with the current source. |
| `POST /api/v1/trajectories/{id}/assistant/proposal/apply` | Body `{expected_revision}`. Three-way merges the proposal onto the current source and saves it as one edit with provenance `assistant_proposal`, then resolves the comments it addressed that still await review. Returns the saved trajectory with an Undo `delta` and the `resolved` count. A conflict returns 409 `proposal_conflict` and changes nothing. |
| `DELETE /api/v1/trajectories/{id}/assistant/proposal` | Drops the proposal, reopens the comments it addressed with a note, and forgets the kept conversation. |

### Stale reviews

Changing the graph, an applicable checklist, Draft evidence or validation settings makes affected signoffs **stale** without changing stages or rewriting
history. Core changes affect Fast and Critical, and Critical-only changes affect Critical. Current
reads show `review_status`, and export's "exclude stale" option uses the same hashes. Nothing is
re-reviewed automatically. A bulk review leaves one Undo/Redo step that restores saved results
without calling a model.

## When things go wrong

- **Transport errors** (after the endpoint's retries): that endpoint **pauses**. Stages and
  attached work are kept. Fix the server and use **Resume endpoint**.
- **Format failures** are retried up to `format_retries` times. Persistent malformed review
  replies can hold the trajectory for attention with no quality verdict.
- **Context and exchange limits** end a pass and keep its edits. Assembly settles as
  Unfinished; a review records no verdict and keeps the stage. A Draft over its endpoint
  context allowance is held for attention.
- **Held trajectories** never stall the rest of the job: the others keep running. A held
  trajectory shows the reason in `metadata.needs_attention` and the job log. When only held
  trajectories remain, the job pauses. **Resume** retries every held trajectory (with a fresh
  bounded correction); **detach** settles one as Unfinished, keeping its edits.
- **Server restart:** jobs pause, callbacks are fenced, unaccepted text is dropped, and you
  resume manually. Completed events and review edits survive.
- **Cancel and detach** use the same fences as everywhere else ([Jobs](GRAPH-JOBS.md)).

## Reference

**Settings.** Under `generation`, `max_authoring_exchanges` defaults to 10,
`review_include_draft` defaults to false and `assistant_context_bytes` defaults to 65536.
The `fast` and `critical` options default to true. Settings saved with the retired `repair`
and `repair_rounds` options still load, and `repair_context_bytes` becomes
`assistant_context_bytes`. Set `disabled_warnings` to warning codes to omit. Prompts are in
`settings.prompts`.

**Endpoints.** Remix sends non-streamed OpenAI-compatible `/chat/completions` requests
by default. Enable **Use Responses API** on an endpoint card to send `/responses` requests.
Draft replies in plain text (no `response_format`, JSON mode or grammar), and Remix stores the
reply as the Draft; it must be at least 200 characters. Assembly, review and the Assistant
require native function tools. Credentials come from `REMIX_API_KEY_<ENDPOINT>` in the environment.

**REST routes:**

| Route | Action |
| --- | --- |
| `POST /api/v1/generation/runs` | `{requests:[{seed, overrides, …}], instructions:{}}`. Needs a Draft role. Returns a job. |
| `POST /api/v1/sampler/runs` | The same driver when a Draft role exists, otherwise a paused snapshot-only run. |
| `GET /api/v1/generation/prompts/default` | The packaged prompts, for Load default. Saves nothing. |
| `POST /api/v1/reviews` | `{query, mode}` or `{trajectory, expected_revision, mode}`. |
| `GET /api/v1/trajectories/{id}` | Source, ownership, diagnostics and review staleness. |
| `/jobs`, `/attempts`, `/results`, `/history` and controls | Progress, skipped targets, saved deltas, Pause, Resume, Cancel and detach. |

MCP offers `generate_graphs` and `review_graphs` over the same services. Generic runs without this
driver's frozen recipe stay paused.

**For developers.** `GenerationService(..., transport)` takes an async transport for
one queue `Ticket` at a time. Draft returns `{output, model, usage?}`. A native turn
returns `{message, model, usage?}` with the assistant's `tool_calls`. Unusable or oversized
replies return `{invalid_output: true}` or `{context_limit: true}`. The original ticket
and accepted history are stored with the job for restart and retry. The offline workflow tests
mock the provider; actual quality and model compatibility require a model run.

### Context scope and local-model budgets

Draft and Assembly receive the sampled proposal. Reviewers inspect event text as needed
using window and event tools. The initial prompts omit frozen matrix and run history.

Each endpoint has two optional token limits, set in Project Settings. Both default to
**-1**, which means no limit: Remix leaves it to the provider.

| Setting | When set | When -1 (default) |
|---|---|---|
| `max_output_tokens` (**Max output tokens**) | Sent as `max_tokens`. It caps **each response**, not the whole pass, and reasoning and tool calls count toward it. A response that hits it is cut off and retried under **Incomplete response retries**. | No `max_tokens` is sent; the provider's own limit applies. |
| `context_window_tokens` (**Context limit**) | Checked before every request, as described below. A request that would not fit is never sent. | No local check. A provider that reports a context error still ends the pass as a context limit. |

The local check exists because some servers silently cut the start of a prompt that is too
long instead of returning an error, which would hide part of the trajectory from the model.

Before HTTP, the transport measures the serialized messages and response or tool schema in
UTF-8 bytes. It estimates input tokens as bytes divided by the endpoint's `bytes_per_token`
(default **3**). English and compact JSON average about 3.5 bytes per token, so 3 keeps a
margin while using most of the real window; **1** can never undercount for byte-backed
tokenizers but rejects many requests that would fit. The request must fit in the context
limit minus the max output tokens (when set, since that room is held back for the reply on
every request) minus 512 framing tokens. See
[Provider options](PROJECT-SETTINGS.md#provider-options).
It is not an exact Qwen chat-template count. No training tokenizer is loaded and no
model endpoint is probed. Configure the actual model context size, rather than inflating
it to bypass this check. Exact serving-tokenizer accounting is a future extension.

#### Prompt order and prefix caching

Requests are laid out so that an inference server with prefix (KV) caching can reuse as
much of the previous request as possible. Caching saves prompt-processing time and cost; it
does **not** enlarge the context window, because cached tokens still count toward
`context_window_tokens`.

- **All endpoints:** the system prompt is the agent prompt, plus the authoring prompt for tool
  sessions. It's the same for every trajectory in a run. The task and data go in the user message.
- **Native turns:** each request extends the accepted conversation with the assistant's
  tool calls, paired tool results and any user-role correction.

When a context limit is set, over-budget requests stop before contacting the model. A native pass settles with any
edits made so far. Frozen runs keep their endpoint settings, including context limits.

### Watch a running job

The existing job log shows timestamped admission, request queue/start, response,
draft and assembly, review verdict and trajectory outcome messages. Each line has a
status level (`info`, `success`, `warning` or `error`) that the dashboard shows as a
colour; `GET /jobs/{job}/logs` returns it as `level` on every item. Elapsed-time
heartbeat lines ("Still waiting for model response") are off by default; set
`JobService.heartbeat_interval` to a number of seconds to bring them back. Queue
messages identify paused endpoints, and retry messages distinguish another attempt
(`warning`) from exhausted retries requiring Resume (`error`). Request failures say why:
the HTTP status with a plain-language hint, the timeout that applied, a refused
connection, or the missing `REMIX_API_KEY_*` variable. When the provider supplies
them, HTTP errors also show the error code, the request field involved (for
example, `reasoning_effort`), and the provider request ID for tracing. Local errors
name the exception type and the file, line and function that raised it, with the
traceback in the server output.
Responses report provider token counts when supplied, and input sizes are bytes,
not estimated token counts.

Logs contain operation, endpoint and trajectory/request/event IDs, timings and
counts. They do not include prompts, model output, raw provider error bodies,
exception messages or keys. Provider codes, field paths and request IDs are
included only when they match the expected identifier formats.
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
| `timeout_seconds` | `120` | Finite number greater than zero, or `-1` for no limit; the limit for each request |
| `reasoning` | `null` | Any non-empty text, or `null` (common values: `none`, `low`, `medium`, `high`) |
| `reconnect_seconds` | `null` | Number of seconds greater than zero, or `null` |

Set `timeout_seconds` to `-1` for endpoints that are very slow and can take hours to
answer. Remix then waits for the reply without any time limit, so a hung connection is
never cut off on its own; cancel the job if you need to stop it.

`reasoning` is sent as the standard `reasoning_effort` request field. `none` asks the
model not to reason; `null` omits the field so the model uses its own default. Because
Remix sets this field, `extra_body` cannot contain `reasoning_effort`. Any string is
accepted and sent unchanged, because models name their levels differently; Remix
does not check it, so use a value your model supports.

`reconnect_seconds` is off by default. When an endpoint pauses because all of its
retries failed, it normally stays paused until you press **Resume**. With this set, it
resumes by itself after that many seconds and tries the waiting requests again; if they
fail again it pauses and waits again. Pressing **Resume** earlier still works.

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
settings and apply to Draft, Assembly, review and Assistant requests using that
endpoint. Edits do not alter an active run. No frontend files or routes changed.
