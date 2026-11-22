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
| Fast Review | **Fast Review** | Checklist review, with repair if it fails. |
| Critical Review | **Critical Review** | Stricter checklist review, with repair if it fails. |

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
`metadata.authoring_signoffs`. Existing jobs keep their frozen generation driver.

### Generate and review in separate sessions

If you can't load your generation and review models at the same time (for example two local
models that don't fit in VRAM together), use the **Phase barriers** checkboxes in
**Generate trajectories**. They are chosen per job, so each run can pause in different places.

| Checkbox | The job waits until every trajectory has... | Resume starts |
|---|---|---|
| Pause before Assembly | finished Draft | Assembly |
| Pause before Fast review | been assembled | Fast review, with its repairs |
| Pause before Critical review | reached Critical review | Critical review, with its repairs |

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

The prompts are project settings, under **Settings → Automatic review and repair → Generation
prompts**:

| Prompt | Used by | What it's for |
|---|---|---|
| **Agent prompt** | Draft and Assembly | How the agent behaves. Add a persona pack here. |
| **Draft instructions** | Draft | How to write the working notes. |
| **Assembly instructions** | Assembly | How to turn the notes into a graph with the native tools. |

**Load Default Prompts** restores the packaged ones (`graph/draft_assembly.yaml`) after asking
you to confirm; nothing is saved until you choose **Save settings**. Runs freeze the prompts
when they start, and each request records a prompt version: the packaged version, plus a short
hash when you've edited them.

Projects from before Draft and Assembly kept their prompts in the Flow. When such a project
opens, a customised Flow agent prompt (for example one with a persona pack appended) moves into
**Agent prompt** once, and the old Flow is then discarded.

## What the model sees

- **Draft and Assembly** see the sampled proposal, tool catalogue, checklists and project prompts.
  Assembly gets the Draft and can inspect the graph it creates.
- **Send to agent** uses the Agent prompt, Draft instructions and Assembly instructions as
  references when editing a trajectory in response to comments. The job freezes these prompts
  when you send the comments and uses its comment-editing workflow for replies.
- **Review and repair** start with topology, diagnostics and the applicable criteria. Use
  `view_event` for causal predecessors and `view_window` for the text in a window. In Review
  Settings, turn on **Include Draft in review and repair** to require Draft-fidelity evidence
  whenever the source has a Draft. It defaults to off.

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

Use `signoff` to record a verdict, evidence and valid event IDs for every requested
criterion. A successful signoff requires zero errors and zero enabled warnings. Configure
warning opt-outs under **Project Settings → Validation**. A completed failing review can
record evidence for repair. Signoffs preserve the source and settings identity, checklist,
tier, model, endpoint and transition.

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

### Repair

A failed review can enter repair on the same tier's model. Repair starts with the failed review
and continues in one native tool conversation. Each accepted exchange saves its edits and
history. Failing criteria keep the repair open so it can continue.

- Use `signoff` with `status: "complete"` after the graph passes every required criterion
  and has no effective diagnostics.
- Use `status: "unfeasible"` with a reason when repair cannot succeed. The trajectory settles
  as Unfinished with its edits and the failure in `metadata.repair_outcomes`.
- The endpoint context limit and **Maximum exchanges per pass** also end the pass with its
  saved edits. The default is ten exchanges. A reply containing several tool calls counts once.
  If the same endpoint hits the exchange limit twice in a job, Jobs logs a warning with the
  setting to adjust for new runs.
- A successful automatic Fast repair can proceed to Critical review.

### Manual review

**Review** and **Review & Repair** run from the trajectory header's **More** menu on the selected saved
revision, or from the dataset's bulk actions on every match of a search across all pages.

The dashboard always uses Standard mode. Force modes are available through the REST API and MCP.

| Mode | Behavior |
| --- | --- |
| **Standard** | Pending uses the first assigned review role. Candidate gets Critical. Failed stages repair at their own tier if repair is on. Everything else is skipped. |
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
| Session | The model sees the source, its diagnostics, causal views and the comments, then uses the repair spot edits: `view`, `inspect`, `patch`, `patch_window`, `remove_event`. Each accepted edit is saved. |
| Finish | `finish` must answer every frozen comment exactly once. `done: true` adds the note as an agent reply and marks the comment addressed, for the user to accept or send back; `false` adds the note as a reply and leaves it open. The model never resolves a comment. |
| Limits | Invalid replies get the usual bounded corrections, then hold. Outgrowing the repair context budget ends the job with edits kept and comments open. |

The stage and review history aren't touched; edits make an earlier review stale as usual. The job's Undo reverts its
edits but not its comment answers.

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
- **Context and exchange limits** settle native authoring passes as failed, preserving their
  edits. A Draft over its endpoint context allowance is held for attention.
- **Held trajectories** never stall the rest of the job: the others keep running. A held
  trajectory shows the reason in `metadata.needs_attention` and the job log. When only held
  trajectories remain, the job pauses. **Resume** retries every held trajectory (with a fresh
  bounded correction); **detach** settles one as Unfinished, keeping its edits.
- **Server restart:** jobs pause, callbacks are fenced, unaccepted text is dropped, and you
  resume manually. Completed events and repair patches survive.
- **Cancel and detach** use the same fences as everywhere else ([Jobs](GRAPH-JOBS.md)).

## Reference

**Settings.** Under `generation`, `max_authoring_exchanges` defaults to 10 and
`review_include_draft` defaults to false. The `fast`, `critical` and `repair` options
default to true. Set `disabled_warnings` to warning codes to omit. Prompts are in
`settings.prompts`.

**Endpoints.** Remix sends non-streamed OpenAI-compatible `/chat/completions` requests.
Draft uses the endpoint's configured JSON output mode. Assembly, review and repair require
native function tools. Credentials come from `REMIX_API_KEY_<ENDPOINT>` in the environment.

**REST routes:**

| Route | Action |
| --- | --- |
| `POST /api/v1/generation/runs` | `{requests:[{seed, overrides, …}], instructions:{}}`. Needs a Draft role. Returns a job. |
| `POST /api/v1/sampler/runs` | The same driver when a Draft role exists, otherwise a paused snapshot-only run. |
| `GET /api/v1/generation/prompts/default` | The packaged prompts, for Load default. Saves nothing. |
| `POST /api/v1/reviews` | `{query, mode, repair}` or `{trajectory, expected_revision, mode, repair}`. |
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

- **Draft and Assembly:** the system prompt is the stage's instructions plus the agent prompt,
  the same for every trajectory in a run. The user message holds the data.
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
the HTTP status with a plain-language hint, a short provider error code when it is a
plain identifier, the timeout that applied, a refused connection, or the missing
`REMIX_API_KEY_*` variable. Local errors name the exception type and the file, line and
function that raised it, with the traceback in the server output.
Responses report provider token counts when supplied, and input sizes are bytes,
not estimated token counts.

Logs contain operation, endpoint and trajectory/request/event IDs, timings and
counts. They do not include prompts, model output, provider error bodies, exception
messages or keys (the provider's error `code` is copied only if it is a short
identifier such as `rate_limit_exceeded`).
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
| `timeout_seconds` | `120` | Finite number greater than zero; the limit for each request |
| `reasoning` | `null` | Any non-empty text, or `null` (common values: `none`, `low`, `medium`, `high`) |
| `reconnect_seconds` | `null` | Number of seconds greater than zero, or `null` |

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
settings and apply to Draft, Assembly, review and repair requests using that
endpoint. Edits do not alter an active run. No frontend files or routes changed.
