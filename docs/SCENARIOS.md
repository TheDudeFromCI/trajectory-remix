# YAML scenario generation

Start with an external scenario directory:

```sh
trajectory-remix --dataset project.sqlite --scenarios-dir examples/scenarios
```

Copy `examples/scenarios/user_problem` into your own directory to customize it.
The loader recursively discovers `flow.yaml`; its containing folder's basename is
its scenario type. Basenames must be unique, even in different branches of the
folder tree. Each scenario contains one `flow.yaml` and Markdown prompts. Prompt
paths are relative to that scenario, must end in `.md`, and cannot escape it.
Invalid YAML, missing prompts, unknown fields, invalid schemas, unknown roles,
unknown dependencies, and dependency cycles fail startup with an explanation.
Restart Remix to load edited scenarios. No Python scenario scripts are executed.

Open **Edit → Generation → Single Sample**, choose a scenario, configure the maximum simultaneous LLM calls (default **1**) and retry attempts
in Generation Settings, and select **Start**.
Configure endpoint URLs, models, completion limits, timeouts, and credentials in
Generation Settings first. YAML step `endpoint` values refer to these endpoint IDs;
the example uses `generator` and `validator`. Structured model outputs use Pydantic
AI's typed model interface with prompted JSON and schema validation, so providers do not need function calling support.
Providers must support OpenAI-compatible streaming chat completions.

The nonmodal progress panel shows active step names, completed step counts, and
colored chronological logs: complete output blocks, information, success, warning, and error. Internal model thinking is omitted. Copy Output copies the full log; Export Log downloads a plain-text `.log` file. Logs and cumulative attempt counts survive starts and cancellation. Total steps expand
as conversation iterations are entered, so the bar is a step count rather than a
time estimate. The dashboard selects the first published draft and refreshes it
as subsequent exchanges, validation, and repairs are committed. **View draft**
returns to it after browsing another trajectory. The panel can be closed and
reopened through Generation → Status without stopping the run.

**Pause** lets active model calls finish and prevents queued/new calls and later
steps from starting. With concurrency greater than one, all active calls finish.
**Resume** continues at the next boundary. **Cancel** cancels active requests,
discards unfinished output, and retains the planning trajectory and checkpoint.
Only that active trajectory is locked against source edits; unrelated samples
remain editable and validatable. Explicit Set State overrides are available at any time.

## Flow format

```yaml
version: 1
system_prompt: You are Kidra, a coordinator.
schemas:
  Message:
    type: object
    properties:
      text: {type: string}
    required: [text]
    additionalProperties: false
roles:
  kidra:
    instructions: Be patient, competent, and direct.
steps:
  response:
    uses: llm.generate
    prompt: response.md
    profile: kidra
    output_schema: Message
    inputs:
      request: A concrete problem
    retries: 1
  message:
    uses: remix.message
    needs: [response]
    inputs:
      source: assistant
      text: ${steps.response.output.text}
  draft:
    uses: remix.draft
    needs: [message]
```

Step names are stable identifiers. `needs` schedules dependencies; it does **not**
add their outputs to a model's context. Only explicitly selected `inputs`, the
selected Markdown prompt, and the optional role profile reach the model. Inputs
are supplied as a labeled JSON data message, never concatenated into privileged
instructions. Each call starts a fresh model context.

References occupy an entire scalar, such as `${steps.idea.output.problem}`.
They select values, including objects and arrays; they are not Python, string
interpolation, or executable expressions. Available roots are:

- `scenario.tags`: the sampled scenario attributes.
- `roles`: sampled profiles; `profile: user` attaches only that role automatically.
- `tools`: public simulated tool schemas, available only when explicitly selected.
- `steps.NAME.output`: local dependency outputs. Declare their dependencies.
- Inside a repeat, `inputs`: explicitly passed parent inputs; `iteration.index`:
  zero-based round; `iteration.history`: prior iteration outputs. A repeat
  can define a `history` mapping of references to select exactly what subsequent
  iterations receive. The example includes public messages and continuation
  assessments, excluding private reasoning and tool plans.

Independent `llm.generate` siblings run concurrently up to the selected request
limit. Draft mutations execute in order. Parallel authoring does not determine
parallelism inside the trajectory: simulated calls specify their own batches.

## Context tags and roles

```yaml
tags:
  complexity:
    - name: simple
    - name: complex
      description: Several interacting constraints.
roles:
  user:
    instructions: Write the fictional user's messages.
    tags:
      behavior:
        - name: polite
        - name: angry
          description: Frustrated and willing to challenge the answer.
          weight: 1
      knowledge:
        - name: novice
        - name: expert
  kidra:
    instructions: Keep Kidra patient, helpful, and concise.
```

Exactly one option is sampled per configured dimension at run start. Every weight
defaults to **1** (uniform); zero disables an option. Empty dimensions, nonfinite
weights, negative weights, and dimensions with no positive options are rejected.
Kidra has no personality sampling in the example, but can use the same tag
configuration as any other role. Additional roles can describe participants,
subagents, or document authors. Profiles guide authoring; they are not inserted
verbatim into Kidra's training context. There is no automatic weight adjustment,
coverage observer, fixed random seed, or promise of repeatable model output.

## Step types

| `uses` | Behavior |
| --- | --- |
| `llm.generate` | Run a Markdown prompt with selected inputs, optional `profile`, and optional named `output_schema`. Without a schema, return text. |
| `flow.repeat` | Run nested `steps` up to `max_iterations` (1–20), exiting when `until` resolves to true. Pass parent data through `inputs`. Unresolved exhaustion is a failure. |
| `remix.novelty` | Compare an `idea` to existing concept summaries, in batches of 24, using the specified prompt. Human samples without concept metadata contribute user-input excerpts. Regenerate through `idea_step` up to `attempts`; use the least-similar candidate if `fallback` is true, except exact duplicates. Record relaxed novelty in metadata. |
| `remix.message` | Append framed `text` from `source: user`, or deliver `source: assistant` through a visible simulated `chat` tool call. Opens the chat window when needed and exposes the tool schema before sending. |
| `remix.tools` | Execute `calls` in the virtual simulator. Each inner list is an independent parallel batch; batches execute sequentially. Return actual simulator outcomes. |
| `remix.draft` | Add optional private `reasoning` and deliver `response` through `chat`, compile, close windows in the published copy, and atomically insert/update this run's draft at level 0. No other samples are available to the writer. |
| `remix.validate` | Compile and evaluate the draft with `prompt` or named `checks`; `level` is 1 or 2. Every check must pass. Optionally repair with `repair_prompt` up to `max_repairs`. Only the draft, findings, and authoring guide reach repair. |
| `remix.reject` | Reject the run with `inputs.reason`. Preserve an existing draft as rejected. |

Novelty currently uses semantic model comparisons rather than a vector index. It
checks summaries across the dataset, so its request count grows with dataset size.
A future indexed retrieval layer can replace candidate selection behind this step.

A simulated tool call is `{tool: calculator, arguments: {expression: "2 + 2"}}`.
Only `subagent` accepts an authored `outcome`. Real files, external services, and
real subagents are never invoked by scenario tool plans. Tool windows remain
available until the response has integrated their results, then close with EOS.

`when` can be a literal boolean or a reference resolving to a boolean. False skips
the step and returns null. `retries` bounds failed step re-execution (0–5).
Failure paths are explicit:

```yaml
on_failure:
  action: fallback
  value: ${steps.previous.output}
```

The default is `action: reject`. A fallback is validated against the step's output
schema. Structural/tool mutations are rolled back before retrying. Model transport
failures leave a published draft pending; malformed content or failed quality
checks can reject it. Cancellation never triggers retry/fallback.

## Validation, inspection, and persistence

Generated drafts carry `metadata.origin: automation`, selected context tags,
role profiles, concept metadata, and a numeric `validation_level`:

- **-3**: unfinished human-authored draft.
- **0**: pending validation.
- **1 / -1**: initial validation passed / failed.
- **2 / -2**: optional frontier validation passed / failed.
- **3**: human approved for training export.

The example runs initial validation only. To add a stronger validator, append a
`remix.validate` step with `level: 2` and its endpoint; optional steps can use
`when`. Deferred frontier review has no dedicated queue UI in this first version.

For focused validation, replace `prompt` with a mapping of check names to
Markdown files:

```yaml
  validate:
    uses: remix.validate
    needs: [draft]
    endpoint: validator
    checks:
      layout: validate-layout.md
      tools: validate-tools.md
      reasoning: validate-reasoning.md
      correctness: validate-correctness.md
    repair_prompt: repair.md
    max_repairs: 2
    inputs:
      require_chat_delivery: true
    level: 1
```

Each check returns `{"valid": true, "feedback": []}` using the same report schema
as single-prompt validation. Checks run in YAML order, respect pause/cancel and
the global model request limit, and log their names and findings individually.
Each receives the step's explicitly selected inputs, the trajectory without
earlier verdicts, and a `compiled` summary containing resolved events, window
lifetimes, anchors, actual tool calls/outcomes, and compiler diagnostics.
They do not receive other checks' opinions. **Every check must pass** to promote
the sample; one failed check retains the negative validation level even if the
others pass. Named results are stored under the evaluation report's `checks`.
Repairs receive findings prefixed with their check names and trigger all checks
again. Completed checks are checkpointed against the exact reviewed content,
so resuming after an API outage or restart does not repeat completed checks.
Existing scenarios using one `prompt` remain supported.

With `inputs.require_chat_delivery: true` (enabled in the bundled example), a
deterministic check also requires a successful, nonempty visible `chat` tool reply
after the latest user message, and rejects assistant text generated directly into
the chat window. This check can veto an LLM's positive verdict and is rerun after
repairs; findings are logged, sent to repair, and stored in the report's `delivery`
field. Invisible authored messages and failed/cancelled sends do not count as
Kidra's delivery. This option is independent of whether validation uses one
prompt or several checks.

The answer prompt writes only the message body. The assembler supplies the
`chat` call, supervises its request tokens, and lets the simulator append the
framed result to chat with external-input masks. The information-gathering tool
planner does not duplicate that final send. Private reasoning has its own prompt
and must not contain a second user-facing answer.

The bundled example checks attachments/window lifecycle, tools/causality,
reasoning/role behavior, and factual correctness separately. Correctness review
verifies the original problem and files independently of the reference answer.
To adopt these checks in a copied external scenario, copy the updated flow and
Markdown files into that folder and restart Remix.

The compiler emits a nonfatal `tool_not_shown` warning with the request's event
path when a visible tool is used without its schema in visible `show_tools`
context. Same-tick input is available before generated requests; later listings,
closed windows, and replaced context do not count. Random additional tools are
checked against the actual expanded listing; invisible setup calls are exempt.
Warnings appear in editor diagnostics and authoring API validation, and are
included in generation review inputs and logs. The scenario writer adds the
planned tools' schemas to a dedicated tools window when assembling each batch.
The compiler also warns with `direct_chat_generation` for generated reply text
in chat (EOS/NOOP controls are allowed), and `tools_in_system` for `show_tools`
in a system window. These diagnostics include event paths and appear in both
editor and MCP validation results without making compilation fail.

Use **Edit → Generation → Approve for training export** after human review.
Content or metadata edits to automated samples invalidate previous evaluation and approval.
Human samples begin unfinished at level -3, including historical samples without an explicit level. Human review promotes them to 3; subsequent edits invalidate that approval. Both
CLI token export and dashboard token export exclude levels other than 3, including
explicitly selected pending samples. Project backups retain all samples.

The last run's rendered input messages, outputs, step states, flow, prompts, and
logs are inspectable through authenticated `GET /api/scenarios/job`. Jobs are
persisted in the project's `generation_jobs` table independently of training data;
credentials are redacted from retained snapshots and logs. Reloading the browser
retains pause/progress state. After a server restart, unfinished jobs are marked
interrupted; select a planning trajectory and use Resume Trajectory Generation
to recover its checkpoint. Individual arbitrary step reruns are not exposed.

The older task-list runner remains available for batch generation and existing
single-task editing integrations. Single Sample uses YAML exclusively; no scenario
folder means an explanatory message, never a silent fallback to legacy generation.

Single-sample failures automatically retry with newly sampled scenario and role tags, up to `single_generation.max_attempts` (default 3). `single_generation.max_parallel` defaults to 1. Both settings are saved with the project. Failed samples remain reviewable; cancellation retains the planning trajectory, checkpoint, and log.

## Manual conditions and diagnostics

Generation Settings contains global endpoint and execution settings; legacy Tasks
and Conditions editors are removed. Scenario definitions and prompts remain in
the external folder. The status panel builds tag selectors from the selected
scenario's YAML pools. Randomization is on by default. Changing a selector turns
it off; every displayed choice is then fixed across retries until randomization
is enabled again. These controls are locked during a run. Clear Log clears only
retained output, without cancelling a job or resetting counters. Unchanged logs
retain their DOM nodes and scroll position; new output follows only when already
at the bottom.

Stage labels are Unfinished (-3), Rejected (-2), Unsuccessful (-1), Pending (0),
Candidate (1), Validated (2), and Ready (3).

Each model request logs its model, request count, elapsed time, observed time to
first generated token, and provider-reported token usage when available. Prompt,
completion, total, reasoning, and cache counters are never inferred from stream
chunk counts. The status samples active requests every two seconds. Live tok/s
uses changes in provider-reported or explicitly estimated stream token counters between samples; average
tok/s uses completion tokens divided by elapsed decode time after the first token.
When providers report usage only at completion, stream token estimates supply
live counters until real usage arrives. Unavailable timings are omitted.

Endpoints may set `context_size` (tokens); zero means unknown. When both capacity
and usage are known, the status displays current-request context usage. Usage
excludes unrelated previous workflow requests because each prompt has a fresh
context. The blue baseline is that endpoint's first request's prompt token count;
it includes inputs, because providers do not separately count system instructions
and tool schemas. Remaining used space is grey through 50%, yellow through 75%,
and red above 75%. Hover for used percentage and counts.

The included user_problem scenario now authors explicit `Idea.files` with virtual
paths and contents. `remix.setup` initializes these through invisible simulated
file writes before the conversation. Solution and user prompts share those
facts, avoiding invented or missing documents and renamed task constraints.
The tool prompt includes exact empty, parallel, and sequential JSON examples.
A failed structured-output step receives its previous output and concrete error
in `retry_feedback` on the next bounded retry; malformed output is still rejected
rather than silently coerced into a different tool ordering.

## Checkpointed resume and API recovery

Every attempt has a planning trajectory and a persisted generation checkpoint from
startup. Stop discards the unfinished model response, retaining the trajectory,
completed step outputs, flow/prompt snapshot, and simulator source. Select that
trajectory and use **Edit → Generation → Resume Trajectory Generation**, including
after a server restart. Completed steps are reused; the interrupted step is run
again. A paused live run can also resume through that menu. Completed/rejected
attempts are no longer planning trajectories and cannot use generation resume.
Resume checks that the saved trajectory, tools, and tokenizer still match; it
refuses to overwrite content edited since the checkpoint. Snapshot credentials
are redacted; endpoint configuration and credentials are read afresh on resume.

**Preferences → Model API retries** configures 0–20 extra requests (default 2)
following an API failure. After those retries, the same generation attempt enters
API-paused state rather than being rejected or automatically replaced. Fix the
endpoint in Generation Settings and use Resume. API pauses do not increment the
generation-attempt count. Ordinary malformed-content/quality failures retain their
separate workflow retry policy.

Only the active trajectory is locked against source edits. Other trajectories
remain editable, validatable, creatable, and deletable through the dashboard and
MCP. History operations are allowed when their changes do not touch the active
trajectory. **Edit → Set State** assigns any of the seven validation states,
including during generation; an explicit active-run state override survives later
machine writes. The generation phase and validation state remain separate.

Provider token counters remain preferred. Until counters are reported, tokenizing
the accumulated output/reasoning stream and rendered prompt with the project's
embedded tokenizer supplies explicitly labelled estimates. This works offline and
counts text tokens rather than stream fragments; it may differ from the endpoint's
model tokenizer. Context gauges are therefore also estimates during fallback.
Real counters replace estimates when available. Current throughput and average
throughput remain separate. Total model time in the status is the sum of request
durations, including retries and resumed work; parallel calls contribute their
individual durations, and paused time is excluded.

The idea's `identifier` is a 3–4 word lowercase slug. Remix keeps its immutable
UUID and allocates a readable dataset ID such as `python-code-review-37`, choosing
the first unused numeric suffix. The progress tooltip shows completed/total steps.
Tag selectors are hidden while randomization is on. View Draft is at the bottom
of the controls and closes the status window after selecting the draft.

Endpoint Test sends a small completion request using the form's current values
and existing session credentials where appropriate, without saving changes.
Resuming edited checkpoint content requires explicit confirmation that generation
may replace those edits; declining leaves the saved job intact. Full model API
exception traces are printed to the server console with configured credentials
redacted, and are excluded from generation output logs.

Replay scheduling accepts `max({N_TICK:7}+1, {N_TICK:8}+1)` and nested maxima to
join independent branches. The compiler waits for all dependencies and preserves
cycle, undefined-anchor, tick-limit, and same-tick visibility checks. Authoring
offsets distribute over operands rather than detaching anchors.
