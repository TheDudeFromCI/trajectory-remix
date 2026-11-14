# Scenario generation and review

Remix can generate new trajectories using a model endpoint you configure, then let
you (or an external agent) review and repair them. **Creation and review are separate
steps:** generation only builds and saves *Pending* drafts, and review is something
you run explicitly afterward.

## Contents

- [Available scenarios](#available-scenarios)
- [Generate samples](#generate-samples)
- [Pause, resume and switch tasks](#pause-resume-and-switch-tasks)
- [Generation log and walkthrough](#generation-log-and-walkthrough)
- [Resume unfinished generations](#resume-unfinished-generations)
- [Fix a failed or skipped draft](#fix-a-failed-or-skipped-draft)
- [Validate existing trajectories](#validate-existing-trajectories)
- [Custom scenarios](#custom-scenarios)
- [API reference](#api-reference)

## Available scenarios

Remix starts with these bundled scenarios; no extra arguments are needed:

| Scenario | Tag | What it generates |
| --- | --- | --- |
| `user_scenario` (default) | `#user_scenario` | A conversation with a user: one workspace request, then three requests per exchange, up to four exchanges. |
| `idle_scenario` | `#idle_scenario` | Autonomous work with no user (TODOs, projects, memory, alarms): a workspace request, then a bounded observe–decide–act loop. |
| `kidra_small`, `kidra_medium`, `kidra_large` | matching tags | Project coordination at three scales. See [Kidra scenarios](kidra-scenarios.md). |

All use *content-first* generation: code supplies trajectory syntax, windows, timing,
tool execution, observations and masks, while the model writes the meaningful notes,
actions and continue-or-stop decisions. The older `user_problem` scaffold, the
task-based generator and the `content_first` demo are retired. Their names still work
as aliases (`user_problem` and `user_scenario_noval` map to `user_scenario`,
`idle_scenario_noval` to `idle_scenario`), and old datasets, checklists and
trajectories stay readable.

## Generate samples

1. Open **Generation Settings** and configure your endpoint, concurrency and
   attempt limits. *Generation Defaults* holds request budgets and synthetic-clock
   defaults.
2. Choose **Edit → Generation → Single Sample** or **Batch**.
3. Pick the scenario, randomized or manual conditions, and a count, then **Start**.

Each finished sample is saved as a **Pending (0)** draft. A count of one means one
*completed sample*, not one model call: a scenario may use many requests and can retry
with fresh attempts up to the attempt limit. Failed or incomplete candidates are kept
so they can be repaired externally.

**Token budgets.** If you set an inference tokenizer directory for the generation
model (under Endpoints), each request is checked against the model's budget, covering
the prompt, completion capacity and a reserve. Leave it blank to skip these checks.
They are also skipped when the tokenizer files or optional dependencies are missing,
with the reason recorded in the sample's provenance. Context is never truncated.

**Structured output.** Endpoints default to native JSON-schema output
(`structured_output: "native"`) with `strict: true`. Rules the backend cannot enforce
(`uniqueItems`, `if/then/else`) are checked locally against the original schema, and
model output is never silently corrected. For servers without native schema support,
choose **Prompt-only JSON (compatibility)** (`structured_output: "prompted"`). If a
native request returns HTTP 400 or 422, generation pauses with its checkpoint kept and
does not downgrade automatically. Change the setting and resume.

## Pause, resume and switch tasks

- **Pause** lets active requests finish and blocks new work. **Cancel** keeps the
  checkpoint and any content so far. API failures pause the run without losing the
  attempt. **Resume Trajectory Generation** continues saved work, even after a restart.
- After pausing, **New task → Create a single trajectory / Create a new batch** (also
  under **Edit → Generation**) lets you switch tasks without losing anything. A new
  single starts with a count of 1, and a new batch with 10. A paused batch keeps its
  original quota, so resuming one of its drafts can continue that batch.
- From a paused batch you can also choose **Resume Unfinished Generations** or resume
  the selected trajectory.

## Generation log and walkthrough

**The activity log** is an append-only, project-wide journal in SQLite. Only **Clear
Log** removes entries. Failed attempts, pauses, resumes, restarts, deletions and
reopening Remix leave it intact. Entries are attributed to their sample, job and batch.
**Copy Output** and **Export Log** read the full journal, including entries newer than
the last dashboard refresh, and clearing does not delete any trajectory or proposal.

**The walkthrough** is a Markdown report of what the model saw and did. Choose
**Generate & download walkthrough** in the generation setup. It runs the normal
pipeline with your selected options, then downloads `generation-walkthrough.md` when
the run finishes or stops (even if it failed, paused for the API, or was cancelled).

It contains the sampled conditions; exact system and user messages; JSON schemas
(original, provider and local constraints); raw responses and returned reasoning;
token usage; step timings, retries and fallbacks; and a summary of attempts and model
time. It leaves out raw trajectories, compiler results, sandbox snapshots and
credentials, which are redacted. It is held in server memory only, so it is lost on
restart, and a disconnect discards it while generation continues. Existing runs cannot
be given a trace after the fact.

## Resume unfinished generations

Find the drafts a resume pass will touch with `@generation:unfinished` (add
`#user_scenario` or `@batch:<id>` to narrow). Missing or corrupt checkpoints stay in the
list so you can recover them, while manual entries and copies of another sample's
checkpoint are excluded. `@generation:complete @state:pending` finds finished drafts
awaiting review.

Choose **Edit → Generation → Resume Unfinished Generations** (or the same button in
Generation Control). No selection is needed. It takes a snapshot of every unfinished
generated draft in the dataset and attempts each once, in dataset order, reusing
completed model requests. It never creates new samples or fills an old batch's quota.
Prompts and conditions come from each checkpoint, current endpoint settings apply, and
semantic validation is not run.

Generation Control shows overall progress, the current draft, and
completed/failed/skipped/remaining counts. **Needs attention** groups issues by draft
with technical details on expansion. Failures keep their proposals and the pass moves
on. Drafts deleted, finished or reassigned before their turn are skipped, and only the
active draft is locked from editing.

Pause, resume and cancel apply to the whole pass. After a cancellation or restart,
running it again continues with whatever is still unfinished.

## Fix a failed or skipped draft

Under **Needs attention**, each entry has a recovery menu:

| Action | What it does |
| --- | --- |
| **Force resume** | Continue from the saved checkpoint and cached outputs, overriding edit and tokenizer/tool compatibility warnings. May replace current replay edits. The result is Pending. |
| **Restart from beginning** | Regenerate everything from the saved flow, prompts and conditions. Keeps the sample's ID and UUID, clears cached steps and approvals, and archives the old version. New output can differ. |
| **Delete sample** | Remove it through normal undo history. The recovery archive and journals remain in the project. |

Unavailable actions are hidden. Missing checkpoints cannot be forced. Restart works
from archived original inputs even without a checkpoint, and very old samples that
lack them offer only deletion. Choices target the exact UUID and source hash, so an
entry edited or renamed in the meantime is never overwritten. Recovery handles one
trajectory and never fills a batch quota. Archives live in the SQLite project, so back
up the project to keep them. MCP `get_generation_record(sample_id, recovery_id=…)`
reads an archived version.

## Validate existing trajectories

Training export includes only **Ready (3)** trajectories. Human samples start as
unfinished (-3), and generated samples as Pending (0). Edit content or metadata and
earlier evaluations are cleared, while the current state (even Ready) is kept.
Both dashboard and CLI exports skip anything not Ready. Use
**Edit → Set State → Ready** after you have reviewed a sample yourself.

**Validate one.** Select a saved trajectory and choose **Edit → Validation →
Validate Trajectory**. Pick a validation endpoint and run. By default it records
**Validated (2)** or **Rejected (-2)**; turn recording off for a report only.
Validation never changes the source unless **Automatically repair issues and validate
again** is on, and it never grants Ready.

With automatic repair, the endpoint applies targeted edits and the full checklist is
re-reviewed after each attempt, up to **Generation Settings → Defaults → Maximum
automatic repair attempts** (default 3, range 1–100). The first review does not
count, and an invalid or empty repair uses up an attempt. Compilable repairs are saved
when the loop ends even if issues remain. Cancellation and provider errors leave
content unchanged.

**Validate all.** **Edit → Validation → Validate All** reviews and repairs every
finished Pending trajectory. It records the final result for each, continues after
individual failures, and skips entries that are no longer Pending. Pause finishes
active requests first, Cancel keeps completed results, and **Resume Validation**
continues the queue, even after a restart.

**What is checked.** One shared service backs the menu, the API and MCP semantic
validation: compiler checks, then bounded layout, tool/grounding, private-reasoning and
correctness reviews. The sample's original saved checklist takes precedence over
changed scenario files, and your current custom guidelines add to it. Samples without
a saved contract use the standard checklist. Idle trajectories need no user reply.
The reviewer may request specific missing evidence over a few rounds, and missing
evidence or malformed reports never count as passes. Resumed reviews reuse earlier
evidence only if the source, checklist, tokenizer, tools and endpoint are unchanged,
and changes during review prevent stale results from being recorded. Journals are kept
in `validation_jobs`, separate from generation.

Set an inference tokenizer for exact token accounting during validation (a generator
with the same model and base URL can be reused). Without one, local budget checks are
skipped, though provider limits still apply.

**Without a model endpoint,** an external agent can use MCP `review_trajectory`,
`get_review_checklist`, `get_guidance`, `repair_trajectory` and
`record_trajectory_review`. See [MCP authoring](MCP-AUTHORING.md#review-and-repair).
`validate_trajectory(semantic=false)` runs compiler checks only.

## Custom scenarios

The bundled flows live in `trajectory_remix/scenario_templates`. To make your own,
copy a folder into a new directory and start Remix with
`--scenarios-dir /path/to/scenarios`, which **replaces** the bundled choices.

A flow folder holds `flow.yaml` and Markdown prompts (basenames must be unique, and
paths cannot leave the root). Invalid YAML, schemas, roles, dependencies and inheritance
cycles stop startup with an explicit error.

- `generation_mode: content-first` is the default.
- Steps order themselves with `needs`. Only explicit `inputs` and `visible_windows`
  reach a model request. References fill a whole YAML scalar, for example
  `${steps.idea.output.files}`. Inside a repeat, use `inputs`, `iteration.index` and
  optionally `iteration.history`.
- Scenario and role tags are sampled from weighted pools, or chosen manually. The
  flow's `tag` defaults to its folder name and is added to `metadata.tags`.
- Step types: `llm.content`, `remix.setup`, `remix.message`, `remix.tools`,
  `remix.assemble`, `remix.draft` and `flow.repeat`. Schema-checked content can drive
  calls, branches, repeats and stop decisions. Parallel model siblings run up to the
  request limit, and `remix.assemble` can release windows when their consumers finish.
  Automatic TODO notes stay compiler-managed.

**Review requirements** belong in the scenario concept and its saved checklist, not in
an executable validation step. The shared review contract is in
`trajectory_remix/review_templates`. A flow can add checks outside the creation graph
with `review_checks`:

```yaml
review_checks:
  coordination:
    prompt: review-coordination.md
    view: correctness
    rules:
      continuity:
        requirement: Preserve task ownership, evidence and actionable next steps.
        scope: sample
        allow_not_applicable: false
```

`view` is one of `layout`, `tools`, `reasoning` or `correctness`. Check names must be
identifiers, and prompts follow the same path rules as creation prompts. Saved
checklists list them as `scenario.<name>` next to the standard checks, and they never
run during creation. Each sample keeps its flow, prompts, sampled conditions and full
checklist so it can still be reviewed after the scenario files change. Reviewers also
receive `metadata.context_tags` as `sampled_conditions`. Legacy `remix.validate` steps
in archived flows are readable as review definitions but deferred, so their endpoints
are never called by the generator.

## API reference

For scripting. Endpoints need the dashboard's session token, like all `/api/` routes.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/scenarios/run` | Start creation, e.g. `{"scenario":"user_scenario","batch_size":3,"max_parallel":1}`. Scenario defaults to `user_scenario`. |
| `POST /api/scenarios/suspend` | Park a paused task. `409` while it is still running, a no-op when idle. `/api/state` shows `can_suspend_generation`. |
| `POST /api/scenarios/walkthrough` | Same options as `/run`; returns the Markdown walkthrough. |
| `GET /api/scenarios/log` | Download the full activity journal. |
| `POST /api/scenarios/log/clear` | Clear the journal. |
| `POST /api/scenarios/resume-all` | Start a dataset-wide resume (`202`). |
| `GET /api/scenarios/resume-all/results?offset=0&limit=100` | Paginated results of the latest pass. |
| `POST /api/scenarios/recover` | `{run_id, position, action, source_hash}` with action `force_resume`, `restart` or `delete`. |
| `GET /api/scenarios/recovery/{recovery_id}` | Read an archived source, checkpoint and outcome. |
| `POST /api/validation/run` | `{sample_id, endpoint?, record?, resume?, auto_repair?}` (`202`). |
| `POST /api/validation/run-all` | `{endpoint?, resume?}` (`202`). Always repairs and records. |
| `POST /api/validation/pause` | `{resume?: boolean}`. |
| `GET /api/validation/status` | Progress and findings. |
| `POST /api/validation/stop` | Cancel the current trajectory, keeping completed results. |
| `POST /api/validate` | Synchronous review of one saved sample. |
| `/api/pause`, `/api/stop` | Control a resume pass. |

`GET /api/state` also reports `unfinished_generation_count` and a `generation_resume`
summary. The old task-based `/api/run` returns HTTP 410. Creation, semantic
validation, tokenizer migration and a resume pass cannot run at the same time.

For the Kidra project scenarios in particular, see [Kidra scenarios](kidra-scenarios.md).
