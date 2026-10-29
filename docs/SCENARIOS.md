# Scenario generation and separate validation

[Creation and independent validation flow (PDF)](generation-validation-flow.pdf).

Start Remix normally; no scenario-directory argument is required:

```sh
trajectory-remix --dataset project.sqlite
```

The installed package includes two standard choices:

- **`user_scenario`** (`#user_scenario`): the former streamlined user variation,
  with all original domain/difficulty pools, user traits and manual selections.
  One workspace request plus three requests per exchange; up to four exchanges.
- **`idle_scenario`** (`#idle_scenario`): autonomous TODO, project, memory and alarm
  work without a user. One workspace request plus a bounded observe–decide–act loop.

Both use content-first generation. The validated `user_problem` scaffold, legacy
task-based generator and `content_first` demo are retired. Old scenario IDs remain
aliases for the surviving standard flows when starting a new run. Saved original
checklists, raw proposals, datasets and completed trajectories remain readable.

## Create samples

Use **Edit → Generation → Single Sample** or **Batch**. Choose the scenario,
randomized/manual conditions and count, then Start. Endpoint configuration,
concurrency and attempt limits are under **Generation Settings**. Generation
Defaults contains request budgets and synthetic-clock defaults. Configure the
optional local inference tokenizer directory for the generation model under Endpoints.
Leave it blank to skip local token-budget checks; unavailable tokenizer files or
optional dependencies also skip checks with a recorded reason.

When an inference tokenizer is available, each content request is checked against
its inference-model budget, including the prompt, total completion capacity and
reserve. Otherwise those checks are skipped without truncating context. Code supplies trajectory syntax, windows,
timing, tool execution, observations and masks. Meaningful notes, actions and
continuation/termination decisions remain generated targets. Compiler checks
always run; semantic validators and semantic repair models never run during
creation. A successful workflow saves a **Pending (0)** draft.

Pause finishes current requests and blocks subsequent work; Cancel preserves the
checkpoint and available content. Resume Trajectory Generation continues saved
work, including after restart. API failures pause without replacing the attempt.
A batch targets completed creation workflows, not approved training samples;
failed/incomplete candidates remain available for external repair.

The API is `POST /api/scenarios/run`, for example:

```json
{"scenario":"user_scenario","batch_size":3,"max_parallel":1}
```

The scenario defaults to `user_scenario` when omitted. `user_problem` and
`user_scenario_noval` resolve to `user_scenario`; `idle_scenario_noval` resolves to
`idle_scenario`. They do not expose alternate generator versions. `/api/run`, the
old task-based creation API, returns HTTP 410. Legacy task settings and automatic
validation flags in imported configurations do not enable a creation pipeline.

## Validate existing trajectories

Select a saved trajectory and choose **Edit → Generation → Validate Trajectory**.
Choose a validation endpoint and run the review. The default records **Validated
(2)** or **Rejected (-2)**; turn off recording for a report only. Validation never
rewrites source and never grants Ready. Human **Ready (3)** approval remains
required for training export.

One service handles the menu, explicit validation API and MCP semantic validation.
Every trajectory uses the same review process: compiler checks followed by bounded
layout, tool/grounding, private-reasoning and correctness checks. The original
saved scenario requirements/checklist take precedence over replacement scenario
files; current custom dataset/scenario guidelines supplement them. Samples without
a saved scenario contract use the standard shared checklist. No user reply is
required for an idle trajectory.

The dialog reports progress and findings independently of generation. Cancellation,
provider failures or unresolved evidence preserve the validation journal without
recording a verdict. Resume reuses completed evidence groups only if source,
checklist, tokenizer, tools and endpoint configuration still match. Changes during
review prevent stale results from being recorded. Start generation and validation
as separate operations; neither starts the other.

- `POST /api/validation/run`: `{sample_id, endpoint?, record?, resume?}`; returns 202.
- `GET /api/validation/status`: progress and final findings.
- `POST /api/validation/stop`: cancel without changing source or approval state.
- `POST /api/validate`: synchronous review of a saved sample by index, with optional
  endpoint/record fields.
- MCP `validate_trajectory(semantic=false)` performs compiler checks only.
  `semantic=true` calls this same service without recording a sign-off.

Semantic review may request specific missing evidence within bounded context
rounds. Missing evidence and malformed reports are not passes. Configure an exact
inference tokenizer for bounded model-specific validation accounting; a matching
model/base URL may reuse the generator tokenizer. Without an available inference
tokenizer, validation also skips local token-budget enforcement. Training-tokenizer
estimates remain analytics only; provider limits still apply. Raw responses, errors and progress are journaled in
`validation_jobs`, separate from `generation_jobs` and generation throughput.

Use MCP `review_trajectory`, `get_review_checklist` and `get_guidance` for API-free
external review. `repair_trajectory` and targeted edits remain independent of
creation and local inference. Review repaired source against its original
requirements before recording a new review. Content edits invalidate prior sign-off.

## Custom flows

The canonical sources are in `trajectory_remix/scenario_templates`. Copy a folder
into a custom root and start with `--scenarios-dir /path/to/scenarios` to replace
the bundled choices. Folders contain `flow.yaml` and Markdown prompts; basenames
must be unique. Paths cannot escape the selected root. Invalid YAML, schemas,
roles, dependencies and cyclic inheritance fail startup explicitly.

`generation_mode: content-first` is the default. Steps schedule dependencies with
`needs`; only explicit `inputs` and `visible_windows` enter a model request.
References occupy an entire YAML scalar, for example `${steps.idea.output.files}`.
Inside a repeat use `inputs`, `iteration.index` and optionally `iteration.history`.
Scenario tags and role tags are sampled from weighted pools, or selected manually.

Available creation primitives are `llm.content`, `remix.setup`, `remix.message`,
`remix.tools`, `remix.assemble`, `remix.draft` and `flow.repeat`. Schema-checked
content can control calls, branches, repetitions and stop decisions. Parallel model
siblings run up to the request limit; independent replay streams/tools overlap.
`remix.assemble` can release model-selected obsolete windows after consumers finish.
Automatic TODO notes remain compiler-managed and persistent.

Put review requirements in the original scenario concept and saved checklist,
not in an executable validation tail. The standard shared review contract lives
under `trajectory_remix/review_templates`. Legacy `remix.validate` tail steps in
archived flows remain readable as review definitions but are deferred during
creation; their endpoints are never required or called by the generator.

See [content requests and recovery](content-first-generation.md),
[idle-work behavior and demo](idle-scenario-generation.md), and [MCP](MCP.md).
