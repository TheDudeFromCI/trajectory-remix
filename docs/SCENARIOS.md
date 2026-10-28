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

## Batch creation

Use **Edit → Generation → Batch**, choose a scenario and set **Trajectories to
create** (1–1,000), then press **Start**. Single Sample opens the same panel with
a count of 1; Batch defaults to 10. Trajectories run sequentially through the
same YAML flow, under the shared LLM request limit. Each completed sample is
available immediately for review; the batch does not approve samples for export.

The batch targets the requested number of successfully completed workflows.
Failed attempts remain in the dataset for inspection and do not fulfill the
target. The total attempt budget is the target multiplied by Generation
Settings' **Maximum attempts per single sample**; exhausting that budget stops
with a partial-result summary. A YAML failure fallback that intentionally lets
a workflow finish counts as completed; the sample still retains its validation
state and only Ready (3) samples export. Attempts/accepted remain cumulative;
model request/time counters still describe only the current trajectory attempt.

Batch progress shows completed/target and used/allowed attempts. **View Batch**
closes the panel and filters the dashboard to this batch, including failed
attempts. Clear the dashboard search to see other samples. A searchable
`metadata.generation_batch` ID groups the samples. Browsing an earlier sample
while generation runs keeps that selection instead of jumping to every new
draft; View Draft returns to the active one. Only the active draft is read-only.

Random conditions are resampled for each new trajectory attempt; manually chosen
tags remain fixed across the batch. Pause waits for current model calls, cancel
keeps completed samples and the unfinished checkpoint, and exhausted API retries
pause the batch without spending a new trajectory attempt. Select the unfinished
planning trajectory and use **Resume Trajectory Generation** to finish it and
continue the remaining batch, including after restarting the server. The saved
flow/prompts also allow continuation without the external scenario folder.

The existing `POST /api/scenarios/run` accepts `batch_size` (default 1). Invalid
counts are rejected before creating a draft. Batch state is persisted with the
current generation checkpoint; no legacy batch/task runner is involved.

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

External MCP agents can discover candidates with
`list_trajectories(query="@state:candidate", limit=5)` and perform their own
review/repairs. After reviewing the final source, `sign_off_trajectory` records
Validated (2) or Rejected (-2), using a required reviewed revision, reviewer label
and summary. This path makes no model API calls; subscription-backed Codex can
do the review itself. Repairs invalidate previous reviews, so sign off after
finishing edits and reviewing the repaired result. Human Ready (3) remains
separate. See `docs/MCP.md` for the call and audit fields.

For bounded validation, define each named check as an object. The bundled
scenario uses these settings (the input paths select values from the validation
step's `inputs`, not from arbitrary project data):

```yaml
  validate:
    uses: remix.validate
    needs: [conversation]
    endpoint: validator
    inputs:
      idea: ${steps.novelty.output}
      roles: ${roles}
      require_chat_delivery: true
    checks:
      layout:
        prompt: validate-layout.md
        view: layout
        inputs: [idea.problem]
        rules:
          context_retention:
            requirement: Keep evidence active throughout dependent generation, or retain sufficient facts elsewhere before closing it.
          attachment_separation:
            requirement: Supply each attachment as separate document context, not whole-file chat prose.
            allow_not_applicable: true
        events_per_chunk: 10
        overlap: 2
        max_input_tokens: 6000
        content_tokens: 800
        context_rounds: 2
      tools:
        prompt: validate-tools.md
        view: tools
        inputs: [idea.problem]
      reasoning:
        prompt: validate-reasoning.md
        view: reasoning
        inputs: [roles.user.tags, roles.kidra.instructions]
      correctness:
        prompt: validate-correctness.md
        view: correctness
        inputs: [idea.problem, idea.success_criteria]
        rules:
          task_fulfillment:
            requirement: The actual request is resolved with a correct, usable result; earlier delivered replies may establish fulfillment.
            scope: sample
    repair_prompt: repair-edits.md
    repair_mode: edits
    repair_max_input_tokens: 10000
    repair_guides: [repair-notes.md]
    max_repairs: 2
    level: 1
```

Object checks default to 10 units per group, overlap 2, a 6,000-token input
budget, 800-token content fragments, and 2 additional context rounds. Settings
are independent per check. `overlap` must be smaller than `events_per_chunk`.
An input selector such as `idea.problem` selects that value only; selecting
`idea` would include all its fields. No evaluation history is supplied.

### Semantic checklists

Define semantic rules under each object check's `rules` in **flow.yaml**. Each
stable rule ID maps to a nonblank `requirement`, optional `scope` (`group`, the
default, or `sample`), and `allow_not_applicable` (default `false`). Rule IDs use
letters, digits, underscores or hyphens, starting with a letter. Duplicate YAML
keys, invalid IDs, unknown fields and invalid scopes are rejected at load time.

The flow's top-level `tag` identifies its generated samples for MCP review.
The bundled flow uses `tag: user_scenario` (search `#user_scenario`); custom flows
default to their folder name. Tags are bare names without `#`, beginning with a
letter and containing letters, digits, underscores or hyphens. Configured scenario
tags must be unique, case-insensitively. Generation adds this tag while retaining
all randomized scenario tag names and `role:trait` tags, plus structured
`metadata.scenario`, `context_tags` and `roles` provenance.

New samples retain their original rules/prompts in `metadata.review_checklist`.
MCP `review_trajectory` fetches it automatically; `get_review_checklist` fetches
just the contract. Older/tag-only samples resolve through the configured scenario
registry (metadata.scenario remains a legacy fallback). `repair_trajectory` applies
atomic targeted fixes, and `record_trajectory_review` records every per-rule result
with revision/checklist-hash checks, without invoking configured model endpoints.
See [scenario MCP review](MCP.md#scenario-review-and-repair). Keep the snapshot on
export/import to preserve the original checklist after scenario files change.

The runner generates an explicit prompt checklist and exact JSON schema from
these same definitions. All rules in a category are reviewed **in the same
request per group**, in YAML order; there is no separate request per rule.
Evidence comes before the verdict in the requested schema. For example:

```json
{
  "checks": {
    "context_retention": {
      "evidence": "replay[12][0] closes the source before replay[13][0] finishes using its values",
      "status": "fail",
      "feedback": "Retain the source until its dependent request finishes."
    },
    "attachment_separation": {
      "evidence": "No attachments are supplied in these events",
      "status": "not_applicable",
      "feedback": ""
    }
  }
}
```

Every configured rule must appear exactly once. Missing/extra rule IDs, an
overall model-authored `valid`/`feedback`, incorrect types, blank evidence,
failures without nonblank feedback, and feedback on passing/inapplicable items
are rejected. `not_applicable` is accepted only for rules that explicitly permit
it and must be justified in `evidence`. It never substitutes for missing context.
The required evidence is a short explanation; failures must cite source event
IDs. This records claimed evidence and coverage, not proof of semantic accuracy.

Statuses are `pass`, `fail`, `needs_context`, and, when enabled,
`not_applicable`. A group-scoped pass covers only the supplied events and their
dependencies, not unseen portions of the sample. A sample-scoped pass must have
enough relevant evidence to establish the whole requirement; absence of a local
counterexample is insufficient. A rule needing omitted evidence returns
`needs_context` and top-level `context_requests`, such as `["window:attachment-0"]`.
Sample-scoped checks can request `sample_window:<id>` for the complete window
history, including events later than the current group. `sample_window:chat`
also includes chat tool requests/results across their tool windows, so a final
acknowledgment can be checked against an earlier task-solving reply. This is
offline review evidence, not permission for Kidra to use future observations.
The same input budget and context-round limits apply; group-only checks cannot
request full sample-window history.
The reviewer gets bounded evidence and reevaluates the complete checklist for
that group. Unavailable/over-budget evidence, missing requests, or unresolved
requests after `context_rounds` block approval rather than triggering speculative
repairs. `review_retries` on the validation step (default 2, range 0–5) allows
bounded correction of malformed reports and invalid, duplicate, or over-budget
context requests. The reviewer receives the error and unchanged evidence, and
can narrow a request to exact unit IDs. No evidence is silently truncated and
the input budget still applies to corrective prompts. All applicable groups and sample requirements must pass; code computes
the overall verdict, so the model cannot override a failed item.

Group reports retain the individual evidence/status/feedback in `checks`.
Category reports expose `rule_results` with each group's checklist. Failed items
become findings prefixed with their rule ID and evidence; category IDs are added
to the combined report. Every category/group is reviewed before semantic repair
begins. Targeted repair receives each failed group's collected findings, then
edits are applied atomically and compiled. All rules are reviewed again after
repair, including previously passing rules and unchanged candidates. The existing
`max_repairs` limit bounds the loop. Rule count increases output tokens and may
reduce how much evidence fits in a group; it adds no per-rule round trips.

These are separate budgets: `context_rounds` bounds successful evidence
expansions per group, `review_retries` bounds failed review responses/requests
per group (or per legacy whole-payload check), and `max_repairs` bounds repair
attempts on the same trajectory. Generic step `retries` and batch `max_attempts`
do not replace either recovery budget. A log saying `attempt 1/1` describes
generic step retries; repair progress is logged as `Repair attempt N/M`.

When review recovery is exhausted, the batch pauses with `evaluation_failed`.
The draft remains Pending (validation level 0), with an `evaluation_error` in
metadata and its generation checkpoint intact. Resume continues that candidate
and reuses completed review groups; it does not sample another idea or mark the
sample unsuccessful. Fixed budget errors can still require a new run with
updated settings: saved checkpoints retain their original flow and prompts.

The bundled checklist covers the semantic guidance in the authoring reference
and [review guide](../trajectory_remix/guides/review.md):

| Category | Semantic rules |
| --- | --- |
| Layout | Actor ownership/visibility/supervision; separate attachments; coherent window purposes; evidence retention across entire dependent streams; timely context release and useful document compaction. |
| Tools | Canonical outcome grounding and partial observations; time-correct knowledge; useful calls and redundant reads; dependency-aware concurrency; scoped specialist histories/artifact versions; artifact/test claims; progress, TODO meanings, error recovery and handoff consistency. |
| Reasoning | Private working notes; useful parallel analysis; sufficient evidence transfer; user/Kidra role consistency; dialogue continuity and author-only knowledge; appropriate coordination/delegation. |
| Correctness | Independent factual/logical verification; literal constraints; counterexamples to extremal claims; usable code/plans/edits; calibrated uncertainty; whole-conversation task fulfillment. |

Compiler-verifiable structure, token/tick constraints, tool-schema visibility and
chat delivery remain deterministic checks, outside the semantic rule list. The
existing Generation Settings task validators and `validate_trajectory(semantic=true)`
still use their separate legacy task configuration; `rules` applies to scenario
`remix.validate` object checks.

| View | Information supplied |
| --- | --- |
| `layout` | Event timing, purpose and lifecycle; chat and attachment paths/sizes. Checklist-enabled checks also receive bounded source contents, reasoning and canonical tool outcomes to assess semantic consumers and evidence retention. |
| `tools` | Requests, canonical simulator outcomes, relevant public tool schemas, chat and reasoning needed to assess causality; checklist-enabled checks also include document/attachment evidence to justify reads. |
| `reasoning` | Private reasoning and conversational behavior; no tool-result bundles or full source metadata. |
| `correctness` | Original task inputs, user messages, authored file evidence, tool observations and prior replies in groups, with the complete final answer shared across passes. |

Groups follow resolved timing. A review unit is a projected source event,
simulator result, or text fragment. Large strings split losslessly, retaining
source `event_id`, JSON-pointer `field` and character `offset`. Generated content
fragments use compiled token offsets for their actual tick spans, including
anchors and logical window references. Other derived fragments explicitly
retain event-wide bounds; reviewers must not assume finer timing. Every unit is
covered; adjacent groups overlap when space permits. If the token budget allows
only one unit, scanning advances by one instead of looping forever.

Each group includes a boundary manifest: open-window lifetimes, pending calls,
visible tool names, and recent history IDs. These are context references, not
invented semantic summaries. A legacy reviewer (without `rules`) can return
`{"valid":false,"feedback":[],"context_requests":["window:tool-0"]}` or request a
specific history unit ID. Checklist reviewers use `needs_context` as described
above. Evidence is supplied in a fresh, bounded request.
For checklist-enabled checks, `context_windows` maps each window ID to
`[open_tick, close_tick]` (null means still open), including closed windows.
Evidence requests can retrieve original documents and observations excluded by
the check's normal view. Requests with `window:` retrieve earlier contents only;
access to historical evidence for offline review does not establish that Kidra
could access it during generation.
Unknown IDs, exhausted context rounds, and evidence that cannot fit produce an
explicit evaluation failure after bounded recovery, never an approval based on unavailable context. Long-range
semantic completeness still depends on the reviewer's evidence requests and
judgment; overlapping groups alone are not a proof of correctness.

Repeated long strings within a request are stored once in `content_pool`, with
`text_ref` references. Payloads and output schemas use compact JSON separators;
whitespace inside authored text, code and files remains intact. The input budget
covers rendered instructions, selected data and the output schema, plus a
128-token framing reserve, using the project tokenizer. Endpoint context size
minus its configured completion allowance can reduce the effective budget.
Shared context or a single unit that cannot fit fails with an actionable budget
error rather than being silently truncated. Counts are estimates when the model
uses a different tokenizer. Each group logs its size, budget, instruction and
timeline token counts. `max_input_tokens` is also available on ordinary LLM and
novelty steps. Novelty checks send concept summaries without authored file
bodies and divide candidate batches to fit their configured input budget.

Checks run in YAML order under the existing request limiter, pause and cancel
controls. Group results are checkpointed against the exact reviewed content;
resuming reuses completed groups. Every group and every check must pass for
promotion. Exact duplicate findings are deduplicated; per-check results include
the group count. Deterministic compiler findings for missing tool schemas,
schemas in system context, or direct chat generation bypass LLM review and
enter repair directly when `repair_mode: edits` is enabled.

Targeted repairs receive failed groups, their findings, and only the selected
Markdown `repair_guides`. They return `edits`, not an entire replay. Supported
actions are `replace`, `delete`, `insert_before`, `insert_after` using an original
source `event_id`, `append` at a tick expression, and `replace_text` using a
JSON-pointer field plus an exact, unique `old` substring and replacement `new`.
IDs remain stable within the edit batch despite insertions/deletions. Duplicate
edits are removed; conflicting whole-event replacements fail. Edits apply to a
copy, then the full candidate is compiled before publication. Completed repair
group outputs survive interruption; after a repair every review runs afresh,
even if edits were empty. Metadata and identity cannot be changed by this schema.

Malformed JSON, invalid pointers, conflicting edits, and compiler failures consume
a repair attempt, retain the last compilable draft, and feed the error into the
next repair via `retry_feedback`. Invalid cached edits are discarded. Only after
`max_repairs` attempts (or when repair is disabled) is a failed candidate rejected.
Provider outages and cancellation preserve the current attempt and remaining
budget for resume. This also applies to full-replay repairs.

Review `data` is a projection, not part of the source: use `/content`, not
`data.content` or `/data/content`. Replacement/insertion `event` values must be
raw replay events with `op` and the required source fields. Never copy review
`id`, `event_id`, `purpose`, `start`, `end`, `tick`, or `data` fields into them.
Moving an event requires delete plus append at a new tick; replace keeps its
existing tick. The repair payload supplies the configured `eos_token`.

The bundled `user_problem` scenario sets `inputs.release_tool_context: true` on
`remix.draft`. Reasoning must preserve the exact evidence needed by the answer;
tool-result windows close after that reasoning finishes and before the chat
request starts. The answer writer receives the retained reasoning rather than
the original observations. Reasoning remains open through delivery, then closes.
Semantic checks still verify evidence transfer and timing. Custom scenarios
default to false and retain tool context through delivery; true requires nonempty
reasoning. The runtime does not guess the last semantic consumer from a window's age.

Legacy `prompt: review.md`, string checks such as `tools: validate-tools.md`, and
`repair_mode: replay` remain supported for custom scenarios and saved checkpoints.
They retain the old whole-payload/full-replay behavior; migrate to object checks
and edit repairs for bounded prompts. Refresh **all** copied example YAML and
Markdown files and restart Remix to adopt the new defaults. Saved runs keep their
original flow/prompts; start a fresh run to use newly edited scenario settings.
Object checks with absent/empty `rules` retain the legacy `valid`/`feedback`
response contract. To adopt per-rule coverage, copy both the updated flow.yaml
and all four review prompts. Previously saved checkpoints keep their original
rules and schemas through their saved flow/prompt snapshot.

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

Use **Edit → Set State → Ready** after human review to approve training export.
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
context. The blue section estimates the **current** request's system instructions
and output schema using the project tokenizer; task inputs are excluded. It is
not the entire first request. Remaining used space is grey through 50%, yellow through 75%,
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
durations for the current trajectory attempt, including API/content retries and
resumed work; parallel calls contribute their individual durations, and paused
time is excluded. Model requests and total model time reset when a fresh
trajectory attempt starts, including automatic retries with a new trajectory.
Pause/resume keeps that trajectory's counters. Attempts and accepted counts are
cumulative across starts, failures, and restarts; accepted increments once when
a generation finishes successfully after validation, not for each validation
stage. Resuming an older checkpoint does not roll back these cumulative counts.

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

## Scenario variations

`user_scenario_noval` extends `user_problem`, retaining its `#user_scenario` tag, input pools, role profiles and original external checklist while using the bounded content-first generator without local validation. It removes reference-solution, novelty-judging and assessment requests and combines grounded notes, the reply and conversation continuation in one request. A one-exchange candidate uses four model calls.

Declare `extends: parent_name` to inherit system/input sections and prompt files. An explicit `steps` mapping defines the child scaffold; `inherit: true` on a step imports that named parent step before field overrides. Schemas merge by name. Only inheritance-related scenarios can share a tag; the dataset retains exact scenario/family provenance for unambiguous MCP review. Unknown/cyclic parents and unrelated duplicate tags remain errors. See [content-first configuration and recovery](content-first-generation.md#fast-user-scenario-variation).
