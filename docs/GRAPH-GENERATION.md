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

1. In **Settings**, add endpoints and assign the **Draft** role (or **Generator**), and
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

Draft and Assembly write a whole episode in two calls, fix its structure, then hand it to
review and repair:

| Stage | Endpoint role | What happens |
|---|---|---|
| Draft | **Draft** (falls back to Generator) | Plain-prose working notes for the whole episode: messages, reasoning, tool calls and their results, parallel work, and what the agent can confirm at the end. |
| Assembly | **Assembly** (falls back to Draft) | One compact JSON spec of windows and events, built from the draft. If the validator reports any error or warning, the same model then fixes its own graph (the *assembly fix*). |
| Fast Review | **Fast Review** | Checklist review, with repair if it fails. |
| Critical Review | **Critical Review** | Stricter checklist review, with repair if it fails. |

Draft and Assembly can share one endpoint, or you can give Assembly a stronger model for better
structure. The Draft and Assembly calls see the proposal, the tool catalogue, the review checklist
(core and critical) and the project's prompts (see [Edit the prompts](#edit-the-prompts)).

### Assembly

Remix builds the graph from the spec. It adds the tool listing event, tool-call IDs on results,
chat deliveries (`:echo` events) and canonical tool arguments. It also settles slips whose
meaning is clear: an `:echo` written for an event that has no delivery means the event itself,
echo events the model wrote are dropped (the builder writes them), and a tools object or a wrapped
reply is unwrapped. Then:

- A spec that still can't be built (an event listed before what it depends on, an unknown window,
  a reused ID, a call with no tool) gets a correction that says how to fix it, up to
  `format_retries` times.
- A graph with any validator error or warning goes to the **assembly fix**: the Assembly model
  sees the graph, every diagnostic, the causal views and the draft, and fixes them with the same
  patch actions as repair (`view`, `inspect`, `patch`, `patch_window`, `remove_event`). It ends
  with `done`, which is refused while structural errors remain, or `give_up`.
- Giving up, running out of corrections or reaching `repair_context_bytes` hands the graph on
  unchanged rather than failing it. Errors still left go to Fast repair; otherwise the graph goes
  to Fast Review. The result is kept in `metadata.assembly_fix` (reason, turns, errors and
  warnings left).

The draft is kept in the trajectory's `metadata.draft`, so you can compare it with the assembled
graph.

### Generate and review in separate sessions

If you can't load your generation and review models at the same time (for example two local
models that don't fit in VRAM together), turn on **Pause after Draft and Assembly (phase barrier)**
(`generation.phase_barrier: true`). A run then works in two phases:

| Phase | What runs | When it ends |
|---|---|---|
| 1. Draft and Assembly | Draft, Assembly and the assembly fix. A trajectory that reaches review **parks** instead. | When every trajectory is parked, held or finished, the job **pauses** and the log says so. |
| 2. Reviews | Resume releases the parked trajectories into Fast Review and Critical Review, with their repairs. | When every trajectory is settled. |

Parked trajectories don't count toward the active-trajectory cap, so a run of 100 drafts and
assembles all 100, not just the first 20. The phase is stored with the job, so it survives
shutting the server down between sessions.

A typical day-and-night cycle with two local models:

1. Give **Draft** and **Assembly** an endpoint for the generation model, **Fast Review** an
   endpoint for the review model on a different server or port, and **Critical Review** a hosted
   endpoint. Turn on the phase barrier.
2. Load the generation model and start a run. Draft and Assembly run; reviews wait.
3. When the job pauses at the barrier, check the log and any trajectories held for attention.
   You can shut everything down now.
4. Next session: start the server, load the review model, open the job and click **Resume**.
   Reviews run until the job completes.

If generation hasn't finished when you want to switch, click **Start reviews now** on the job
(`POST /api/v1/jobs/{id}/phase/review`). Assembled trajectories go to review; unfinished ones carry
on with Draft and Assembly once their endpoint is back. An endpoint whose server is offline pauses
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
| **Assembly instructions** | Assembly | How to lay the notes out as the compact spec, with a worked example. |

**Load default prompts** restores the packaged ones (`graph/draft_assembly.yaml`) after asking
you to confirm; nothing is saved until you choose **Save settings**. Runs freeze the prompts
when they start, and each request records a prompt version: the packaged version, plus a short
hash when you've edited them.

Projects from before Draft and Assembly kept their prompts in the Flow. When such a project
opens, a customised Flow agent prompt (for example one with a persona pack appended) moves into
**Agent prompt** once, and the old Flow is then discarded.

## What the model sees

- **Draft and Assembly** see the sampled proposal, the tool catalogue, the checklists and the
  prompts. Assembly also sees the draft. Neither sees source metadata or review history.
- **The assembly fix** sees the assembled graph (without metadata), every diagnostic, the
  causal views and the draft.
- **Reviewers and repairers** see the graph, the checklist and the causal view at each event:
  which earlier events it can see, by window. They judge each event only by what it could see.

Synthetic tools are never executed. A tool result is whatever Draft wrote as its example output.

## Tools and chat during generation

- Assembly chooses the tools the agent is shown. Remix writes a **Show tools** listing before the
  opening event: the chosen tools plus a repeatable random selection of others.
- Tool calls and results are ordinary events. Results are free-form text.
- A `chat` call delivers its `content` to the chatroom automatically, as a masked
  `chat_message` with the ID `<call id>:echo`. A person's reply follows that echo. A delivery
  that differs from the submitted content is a structural error, including after repair.
- Malformed chat arguments only warn, and no echo is invented.

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

A failed review enters a Repair & Validate session on that tier's own model. The model sees the
source, the checklist and the failed review, plus each exchange so far. **A repair only fails when
the model gives up**, saying why repair is not feasible.

| Action | What it does |
|---|---|
| `view` | Returns the current source with diagnostics, including earlier patches. |
| `inspect` | Returns one event and the causal view it can see. |
| `patch`, `patch_window` | Merges a patch into one event or window, using the expected source hash. A new event ID adds an event. |
| `remove_event` | Deletes an event. Broken references stay for you to fix. |
| `verdict` | A full checklist verdict. A passing verdict ends the session; a failing one keeps it open with the failing items listed. |
| `give_up` | Ends the repair as failed, with the model's reason (`unfeasible`). |

When a session's transcript reaches `repair_context_bytes` (default 65,536), or the provider's
context runs out, a fresh **round** starts on the current graph with the last failed review. The
`repair_rounds` setting (default 3) caps the rounds as a spending guard; reaching it fails the
repair as `round_limit`.

- Accepted patches save immediately, even if they leave editable errors.
- Accepted patches save immediately, even if they leave editable errors. A passing final verdict
  needs a structurally valid graph.
- When the repair gives up or reaches the round cap, the trajectory is detached as Unfinished
  with its edits and any real verdict evidence kept. The reason is in `metadata.repair_outcomes`.
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
  go to repair. For review and repair, that trajectory is **held** with no quality verdict.
- **Local wrapper problems** and **context limits** hold that trajectory too, instead of asking a
  model to fix programmatic envelopes.
- **Held trajectories** never stall the rest of the job: the others keep running. A held
  trajectory shows the reason in `metadata.needs_attention` and the job log. When only held
  trajectories remain, the job pauses. **Resume** retries every held trajectory (with a fresh
  bounded correction); **detach** settles one as Unfinished, keeping its edits.
- **Server restart:** jobs pause, callbacks are fenced, unaccepted text is dropped, and you
  resume manually. Completed events and repair patches survive.
- **Cancel and detach** use the same fences as everywhere else ([Jobs](GRAPH-JOBS.md)).

## Reference

**Settings** (`generation` in project settings): `phase_barrier` (default false),
`fast`, `critical`, `repair` (default true), `format_retries` (default 2), `repair_context_bytes`
(default 65,536, a transcript bound per repair round, not a token estimate) and `repair_rounds`
(default 3). Configure the `chat` tool with a JSON object input that has a string `content`
to enable the echo helper ([example config](../examples/graph_local_llm.json)). The prompts are
`settings.prompts`: `actor`, `draft` and `assembly`.

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
| `POST /api/v1/generation/runs` | `{requests:[{seed, overrides, …}], instructions:{}}`. Needs a Draft or Generator role. Returns a job. |
| `POST /api/v1/sampler/runs` | The same driver when a Draft or Generator role exists, otherwise a paused snapshot-only run. |
| `GET /api/v1/generation/prompts/default` | The packaged prompts, for Load default. Saves nothing. |
| `POST /api/v1/reviews` | `{query, mode, repair}` or `{trajectory, expected_revision, mode, repair}`. |
| `GET /api/v1/trajectories/{id}` | Source, ownership, diagnostics and review staleness. |
| `/jobs`, `/attempts`, `/results`, `/history` and controls | Progress, skipped targets, saved deltas, Pause, Resume, Cancel and detach. |

MCP offers `generate_graphs` and `review_graphs` over the same services. Generic runs without this
driver's frozen recipe stay paused.

**For developers.** `GenerationService(..., transport)` takes an async single-invocation transport.
It receives a queue `Ticket`, sends only `payload.messages` and `schema` using the pinned endpoint
config, and returns `{output, model, usage?}`, `{invalid_output: true}` or `{context_limit: true}`.
Each Draft, Assembly, assembly fix, review and repair turn is a separate FIFO item, and its original payload is kept for retry
while response bodies are dropped on interruption. Provider usage is evidence only, not a source metric.
Checks: `python -m pytest -q tests/test_graph_generation.py tests/test_graph_jobs.py
tests/test_graph_sampling.py tests/test_graph_studio.py tests/test_graph_export.py` and
`python tests/run_generation_frontend.py`. They use tiny fake-provider sources. Model quality,
tool coherence, schema compatibility and context limits need the user-machine checks.

### Context scope and local-model budgets

Draft and Assembly receive the **sampled proposal**, not the entire sampling matrix.
Source metadata (including its frozen matrix, provenance snapshots and review history)
is retained in storage but excluded from their prompts. Reviewers receive each
source body once; their per-decision causal views list surviving event IDs by window,
including replacement and closure effects. These are references to the source, not
summaries that can introduce new facts.

Endpoint settings now accept `context_window_tokens` (default **65536**) and
`max_output_tokens` (default **8192**). The transport sends the latter as `max_tokens`.
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
much of the previous request as possible. Caching saves prompt-processing time and cost; it
does **not** enlarge the context window, because cached tokens still count toward
`context_window_tokens`.

- **Draft and Assembly:** the system prompt is the stage's instructions plus the agent prompt,
  the same for every trajectory in a run. The user message holds the data.
- **Assembly fix and repair:** each turn resends the session so far and appends the newest
  action and its result, so every request extends the previous one.

The roles always alternate (system, then user): the JSON-mode schema is joined to the
user message rather than sent separately, because some open-weight chat templates
reject two user messages in a row. `tests/test_prompt_cache_order.py` checks this.

Over-budget requests return a context-limit result before contacting the model. Draft and
Assembly hold their trajectory, with operation, input size and allowance in the job log; the
assembly fix hands the graph on; repair starts a fresh round. Nothing is silently truncated or
converted into a quality failure. Frozen runs keep their endpoint settings: start a new run to
apply changed budgets.

### Watch a running job

The existing job log shows timestamped admission, request queue/start, response,
draft and assembly, review verdict and trajectory outcome messages. Requests that are
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
settings and apply to Draft, Assembly, review and repair requests using that
endpoint. Edits do not alter an active run. No frontend files or routes changed.
