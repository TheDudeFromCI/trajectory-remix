# Kidra project scenarios

Select `kidra_small`, `kidra_medium` or `kidra_large` in **Edit → Generation →
Single Sample/Batch**. All are bundled with the installed application. The existing
`user_scenario` and `idle_scenario` remain available, and `user_scenario` remains
the default. Kidra scenarios use the same endpoint settings, manual condition
controls, checkpoint recovery, search and separate validation as other scenarios.

These scenarios translate the attached *Kidra Training Specifications Combined*
packet (Practical Competency Guide v0.2, Persona Guide v0.1, October 2026, plus
training-set recommendations) into project authoring conditions and independent
review requirements. The packet is design input for fictional samples, not an
instruction to execute its example projects in the host workspace.

| Choice | Project scale | Episode decision cap | Intended coordination |
| --- | --- | --- | --- |
| `kidra_small` | One deliverable, 1–3 tasks | 10 | At most two active specialists; avoid elaborate teams |
| `kidra_medium` | 4–8 tasks, 2–3 milestones | 16 | At most three active specialists; linked deliverables |
| `kidra_large` | 9+ tasks, multiple teams, 3+ milestones | 20 | At most six active specialists; critical slice of a longer project |

Scale describes the project, not a minimum transcript length. A large project may
span weeks or months in its supplied history, while one sample advances a bounded
milestone. It must preserve the remaining project as an actionable handoff rather
than pretend that a long plan finishes the project. Do not add tool calls merely
to reach a length target. Caps are safety bounds; a legitimate earlier finish is
valid. The coordinator can stop after verified delivery, an authorized meaningful
milestone with continuity, or a genuine blocker with no useful authorized action.

## Lifecycle and coverage

Each choice offers every lifecycle phase independently of primary competency:

| Lifecycle condition | Default weight | Concrete initial state |
| --- | --- | --- |
| `initiation` | 25 | Goal, constraints, unknowns and an actionable first deliverable |
| `active execution` | 50 | Existing task ledger, completed work, artifacts, prior decisions and remaining resources |
| `recovery` | 9 | Failed acceptance evidence or a blocked prerequisite with a diagnostic/repair route |
| `revision` | 8 | Previous baseline plus an explicit scope/constraint change and affected dependencies |
| `completion` | 8 | Candidate deliverables, acceptance criteria and remaining verification/delivery work |

This implements the appendix's initial 25/50/25 lifecycle split. The internal
9/8/8 split of the final group is an implementation choice. Primary competency
weights are strategic planning 20, task decomposition 20, agent orchestration 20,
context management/continuity 15, quality assurance/replanning 15 and resource
selection/optimization 10. They are initial sampling targets, not measured optimal
training distributions or guarantees about a finite batch.

Independent conditions also vary domain, recurrence and complication. Recurrence
covers one-off work, recurring work, uncertain demand and changed investment
returns. Complications include ordinary coordination, blocked dependencies,
conflicting reports, incomplete handoffs, failed acceptance and resource pressure.
Strategic investment therefore crosses all primary competencies. Expected reuse,
per-use savings, initial/maintenance cost, opportunity cost, reliability and
uncertainty inform decisions; investment, deferral and declining automation can
all be correct. Variation should not force every persona trait into every scene.

Use manual conditions to select exact coverage; randomized draws do not balance a
corpus automatically. Sample tags and `metadata.context_tags` preserve the selected
axes for search and external evaluation. Search for e.g. `#kidra_large #recovery`
or `#kidra_medium @generation:unfinished`. Stratify held-out evaluation by scale,
lifecycle, primary competency, recurrence, resource constraints, active-agent count
and failure type; use observed failures to revise weights. Automatic stratified
batch scheduling and per-stratum evaluation dashboards are not included here.

For example, start a randomized batch through the existing API:

```json
{"scenario":"kidra_medium","batch_size":3,"max_parallel":1}
```

For exact conditions, clear **Randomize tags for each trajectory** in Generation
Control or send a complete
`tags` map (all scenario dimensions and the empty `kidra` scope):

```json
{
  "scenario": "kidra_small",
  "batch_size": 1,
  "tags": {
    "scenario": {
      "project_size": "small",
      "primary_competency": "quality assurance and replanning",
      "lifecycle": "recovery",
      "domain": "software delivery",
      "recurrence": "one-off work",
      "complication": "failed acceptance"
    },
    "kidra": {}
  }
}
```

## Execution and continuity

One content request authors the initial user request, virtual files and optional
TODO/memory/clock seeds. The user request is delivered as a real external user
message. Initial files remain unread; author-only situation and success criteria
never enter Kidra's decision inputs. Briefs specify the goal, available resource
roster, budget/deadline, acceptance criteria, current task ownership/dependencies,
source authority and authorization. This makes an inherited project a concrete
state rather than a new-project prompt with a different label.

Each later request sees only open windows and exact public tool contracts. It
produces private notes, one independent tool batch, context releases and a stop
decision. Follow-up decisions observe canonical results before depending on them.
Tool calls and concurrent reasoning use the existing compiler's replay timing,
loss masks and actual simulated observations. Staggered completion durations are
supported within committed batches. Each batch is joined before the next model
decision; reacting while another batch member is still running is not supported.

Specialist work uses the existing synthetic `subagent` tool with authored outcome
records. Its outcome is an external masked observation, never a Kidra-generated
claim of success. Specialists receive only their task, retained conversation and
explicit shared artifact versions. Reports cannot publish files or execute host
code. The coordinator can copy a proposed artifact through actual file tools,
then request an independent check of the published version. Reviews and tests
remain synthetic; successful generation establishes replay mechanics, not the
quality of real specialist execution.

Resource tiers and costs are scenario facts expressed in assignments and project
ledgers. The existing subagent API does not route real model workers or meter
their fictional costs. The configured overall tool-call cap remains enforced
(default 64 visible calls); no scenario silently raises it. Reserve actions for
verification, checkpointing, user delivery and observing that delivery. Increase
endpoint completion/context limits when necessary for substantive workspace
records; enabling the optional exact inference tokenizer checks model-specific
request budgets. Required evidence is never silently truncated.

Retained private notes and durable project checkpoints preserve current objectives,
owned tasks, dependencies, decisions, evidence versions, remaining budget, blockers
and next steps before obsolete windows close. A verified checkpoint can seed a
later project's authoring state. This is distinct from Remix's generation
checkpoint, which resumes an interrupted construction with its original prompts,
conditions and completed requests. Automatic chaining of independently generated
samples into one persistent simulated project is not part of this change.

## Separate review and limits

Creation performs schema/compiler checks and saves Pending samples. It never
calls semantic validation or grants training approval. `finished=true` with tool
calls and authored outcomes on non-subagent calls are rejected locally before
execution. Exceeding the decision cap retains an unfinished resumable draft and
its proposals rather than marking the project complete.

Kidra adds three top-level `review_checks` to the four shared review categories:
coordination, strategic investment and persona. They are outside the executable
creation graph and saved with the sample. Review checks cover goal alignment,
proportionate delegation, ownership, dependency correctness, resource selection,
context handoffs, verification, targeted recovery, continuity, progress,
authorization and evidence fidelity. Investment is judged using information
available at the time, with inapplicable criteria marked explicitly. Persona is
reviewed separately and cannot compensate for incorrect or unauthorized work.

Use **Validate Trajectory** or the existing MCP review tools for explicit review.
Human Ready approval remains required for training export. Scenario semantics,
team-size limits, fictional budgets, independent-review quality and persona are
review requirements, not guarantees made by the schema. Regression tests use
scripted content to exercise causal repair, parallel specialists, artifact-version
review, blocked handoffs, context release, cancellation/resumption, retained
failures and saved checklists. They do not establish real-model training quality
or long-horizon performance.
