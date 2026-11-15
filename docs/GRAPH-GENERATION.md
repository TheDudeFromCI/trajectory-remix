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

1. In Project Settings, add endpoints and assign the **Generator** role (and optionally
   **Fast** and **Critical** review roles). See [Project settings](PROJECT-SETTINGS.md).
2. Open **Sampler Matrix → Proposal preview → Generate trajectories**.
3. Choose any **pins**, a **seed** and an **attempt count**, then **Start generation**.

Each attempt uses `seed + ordinal`, so runs are reproducible. Pins are overrides for this
request only. They don't change weights or dirty the matrix. Save matrix or metric edits
**before** starting, because the run freezes its inputs at admission, and later edits to
settings or pins don't change its remaining attempts.

The job dialog shows pending, active and finished outcome counts, and manual endpoint Resume
controls. A run of N attempts reserves N identities and ends with N outcomes, including
failures. There are no automatic replacements.

## How a trajectory is built

The sampler picks a **proposal**: a scenario with domain, lifecycle, complexity, user traits
and so on. A **Flow** then builds the trajectory one event at a time, and each provider call
writes exactly one event's content. The Flow, not the model, owns IDs, dependencies, masks,
delivery and closing, so the model never has to copy them correctly.

The default Flow is a compact baseline. It branches on the scenario: hands-off work can start
with external context, collaborative work includes another participant, relevant tags add
external updates, and then reasoning and an optional chat submission follow. It does **not**
claim that every sampled target has been achieved, and targets are kept separate from measured
results. Iterate on prompts with a local model before using output as training data.

Each finished event and its checkpoint commit together, so interrupted work resumes from the
last whole event.

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

For each event, the prompt contains only **causal ancestors**, folded into the windows that are
currently visible, with replacements applied and closed windows removed. Parallel and future
events, metadata, the full proposal, hidden world data and review history are all excluded. There
is no rolling chat history that could smuggle lost context back in.

## Tools and chat during generation

- Tool calls and results are written like any other event. Results are free-form, with
  independent begin/end, instant or streamed delivery, and closing flags.
- A completed `chat` call with `echo_chat=true` produces a masked `chat_message` from the call's
  parsable `content`, linked by `call_id`. A pending-helper checkpoint makes restarts safe.
  Later tasks that depend on the call resolve through its echo.
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

**Review & Repair** works on a selected saved revision, or on every match of a search across all
pages.

| Mode | Behavior |
| --- | --- |
| **Standard** | Pending gets Fast only. Candidate gets Critical. Failed stages repair at their own tier if repair is on. Everything else is skipped. |
| **Force Fast / Critical** | Ignores stage limits. A pass keeps a higher existing stage, and failures follow normal transitions. |

Ownership is always enforced, and a locked source is skipped. Closing the dialog doesn't stop the
job.

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
