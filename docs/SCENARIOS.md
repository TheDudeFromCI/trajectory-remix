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
discards unfinished output, and removes this run's provisional trajectory. It
never overwrites or deletes a draft that another process has changed. Source
editing and approval are disabled during generation; selection, replay, inspection,
and downloads remain available.

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
| `remix.message` | Append framed `text` from `source: user` or generate a framed assistant message. |
| `remix.tools` | Execute `calls` in the virtual simulator. Each inner list is an independent parallel batch; batches execute sequentially. Return actual simulator outcomes. |
| `remix.draft` | Add optional `reasoning` and `response`, compile, close windows in the published copy, and atomically insert/update this run's draft at level 0. No other samples are available to the writer. |
| `remix.validate` | Compile and evaluate the draft with `prompt`; `level` is 1 or 2. Optionally repair with `repair_prompt` up to `max_repairs`. Only the draft, findings, and authoring guide reach repair. |
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
interrupted and their published drafts remain pending/reviewable. Automatic
recovery or rerunning individual saved steps is not implemented yet.

The older task-list runner remains available for batch generation and existing
single-task editing integrations. Single Sample uses YAML exclusively; no scenario
folder means an explanatory message, never a silent fallback to legacy generation.

Single-sample failures automatically retry with newly sampled scenario and role tags, up to `single_generation.max_attempts` (default 3). `single_generation.max_parallel` defaults to 1. Both settings are saved with the project. Failed samples remain reviewable; cancellation discards only the current provisional sample and retains the log.
