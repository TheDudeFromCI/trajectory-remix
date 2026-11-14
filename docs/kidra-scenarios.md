# Kidra project scenarios

The `kidra_small`, `kidra_medium` and `kidra_large` scenarios generate trajectories
in which Kidra coordinates a project: planning, delegating to specialists, checking
their work, recovering from problems and handing off cleanly. Pick one in
**Edit → Generation → Single Sample** or **Batch**. They use the same endpoint
settings, condition controls, checkpoints, search and separate validation as every
other scenario (see [Scenario generation](SCENARIOS.md)). The default remains
`user_scenario`.

They turn the *Kidra Training Specifications Combined* packet (Practical Competency
Guide v0.2 and Persona Guide v0.1, October 2026) into sampling conditions and review
requirements. The packet's example projects are inspiration for fictional samples;
nothing here runs against your real workspace.

## Choosing a scale

| Scenario | Project | Max decisions | Coordination |
| --- | --- | --- | --- |
| `kidra_small` | One deliverable, 1–3 tasks | 10 | At most two active specialists |
| `kidra_medium` | 4–8 tasks, 2–3 milestones | 16 | At most three active specialists; linked deliverables |
| `kidra_large` | 9+ tasks, several teams, 3+ milestones | 20 | At most six active specialists; one critical slice of a longer project |

Scale describes the *project*, not how long the transcript must be. A large project
may span months in its supplied history, but one sample advances a bounded milestone
and leaves the rest as an actionable handoff. The decision cap is a safety bound. The
coordinator may finish earlier after verified delivery, an authorized milestone with
continuity, or a real blocker with nothing useful left to do. Do not add tool calls
just to look longer.

## Conditions you can sample

Each choice offers all of these independently:

**Lifecycle** (default weight): `initiation` (25), `active execution` (50),
`recovery` (9), `revision` (8) and `completion` (8). Each starts from a concrete state,
such as a goal with unknowns, an existing task ledger, failed acceptance evidence, a
changed scope, or candidate deliverables awaiting verification.

**Primary competency** (weight): strategic planning (20), task decomposition (20),
agent orchestration (20), context management and continuity (15), quality assurance
and replanning (15), and resource selection and optimization (10).

**Also varied:** domain, recurrence (one-off, recurring, uncertain demand, changed
investment returns) and complication (ordinary coordination, blocked dependencies,
conflicting reports, incomplete handoffs, failed acceptance, resource pressure).

These weights are starting targets, not proven optimal training mixes, and a finite
batch will not match them exactly. Investment, deferral and declining to automate can
all be correct answers, depending on expected reuse, savings, costs and uncertainty.

**Controlling coverage.** Random draws do not balance a corpus, so choose conditions
manually when you need exact coverage. Clear **Randomize tags for each trajectory** in
Generation Control, or send a complete `tags` map to the API (every scenario dimension
plus the empty `kidra` scope):

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

A random batch is just `{"scenario":"kidra_medium","batch_size":3,"max_parallel":1}`.
Samples are tagged and keep `metadata.context_tags`, so you can search for
`#kidra_large #recovery` or `#kidra_medium @generation:unfinished`. Automatic
stratified batching and per-stratum evaluation dashboards are not included, so
stratify held-out evaluation yourself (scale, lifecycle, competency, recurrence,
resources, active-agent count and failure type).

## How a sample is built

1. **Workspace.** One request writes the user's request, virtual files and optional
   TODO, memory and clock seeds. The request is delivered as a real external message.
   Initial files start unread, and the author-only situation and success criteria
   never reach Kidra. Briefs state the goal, resources, budget and deadline,
   acceptance criteria, task ownership, authority and authorization, so an inherited
   project is concrete state rather than a renamed new project.
2. **Decisions.** Each later request sees only open windows and public tool
   contracts, and produces private notes, one independent batch of tool calls,
   context releases and a stop decision. Results come from the real simulated tools
   and replay timing. Batches may complete at staggered times, but each batch is
   joined before the next decision.
3. **Specialists** are the built-in `subagent` tool with authored outcomes, delivered
   as masked external observations, never as Kidra's own claims. A specialist sees only
   its task, its conversation and explicitly shared file versions, and cannot publish
   files or run code. Kidra can copy a proposed artifact with file tools and then ask
   for an independent check of that version. All reviews and tests are synthetic, so a
   successful generation proves the replay mechanics, not real specialist quality.
4. **Resources.** Tiers and costs are scenario facts in assignments and ledgers. The
   subagent API does not route real workers or meter costs. The overall visible
   tool-call cap still applies (default 64), so keep calls in reserve for verification,
   checkpointing, delivery and confirming delivery. If workspace records are large,
   raise the endpoint's completion and context limits. Required evidence is never
   truncated, and the optional inference tokenizer enables exact budget checks.
5. **Continuity.** Private notes and durable checkpoints keep objectives, ownership,
   dependencies, decisions, evidence versions, budget, blockers and next steps before
   old windows close. A verified checkpoint can seed a later project's authoring state.
   This differs from Remix's generation checkpoint, which resumes an interrupted
   build. Automatically chaining independent samples into one persistent project is
   not supported.

If a sample reaches the decision cap without finishing, it is kept as an unfinished,
resumable draft rather than declared complete. A `finished=true` decision that also
makes tool calls, or authored outcomes on non-subagent calls, are rejected before
execution.

## Review

Creation only compiles and saves a Pending sample. It never runs semantic validation
or grants approval, and Ready approval stays a human step. On top of the four shared
review categories, Kidra scenarios add three `review_checks`: **coordination**,
**strategic investment** and **persona**. These cover goal alignment, proportionate
delegation, ownership, dependencies, resource choice, handoffs, verification, targeted
recovery, continuity, progress, authorization and evidence fidelity. Investment is
judged on the information available at the time, with inapplicable criteria marked.
Persona is reviewed separately and cannot make up for incorrect or unauthorized work.

Run **Validate Trajectory** or the MCP review tools to apply them. Team-size limits,
fictional budgets, independent-review quality and persona are review requirements,
not schema guarantees. The regression tests use scripted content to check mechanics
such as repair, parallel specialists, version-pinned review, handoffs, context release
and resumption. They say nothing about real-model training quality.
