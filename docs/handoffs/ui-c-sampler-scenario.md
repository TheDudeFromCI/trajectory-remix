# UI C — Scenario-first sampler editor

Read [CONTEXT](CONTEXT.md), handoff 04 and contracts **§8–10**. Branch:
**`design/sampler-scenario`**. Requires the same shared 03/04 backend as UI A/B.

## Design thesis

Lead with an understandable sampled scenario brief, then expose the settings that
control each attribute. Help the user see the consequence of matrix edits instead
of first requiring them to understand the entire dense ontology.

## Proposed implementation

- Dedicated Sampler Matrix tab with a human-readable proposal/brief and linked
  attribute cards. Clicking a competency/scenario attribute opens its controlling
  fields, options, weights and constraints.
- Use explanatory labels, numeric values/units and clear separation between a
  project-wide weight edit, a per-run pin and one preview's sampled outcome.
- Fixed seed comparisons make before/after effects inspectable; changing a seed
  shows variation. This is a proposal preview, not generated text/actual metrics.
- Provide a complete searchable settings index alongside the narrative view so
  rare/custom settings do not disappear merely because the current preview does
  not exercise them. Preserve all unknown/custom fields.
- New project matrix/metric definitions affect future runs only; existing targets
  and active-run snapshots remain untouched.

## Shared constraints

No extra LLM to write the brief, no tokenization, no invented statistics or
adaptive balancing. Render the existing sampler's structured output. Same API,
schemas, fixtures and persistence as the other variants; don't fork core behavior.
Keep normal application layout and the existing metrics panel intact.

## Review and correctness

Use tiny fixtures: preview/seed/pin behavior, linked field editing, complete-index
access, save round trips, unsupported/custom settings and future-run isolation.
Capture screenshots showing how a user changes a scenario attribute and discovers
its settings. No large sampling/performance or live-model experiment.

Deliver a draft UI PR with workflow/tradeoff notes and review questions. Expect
revisions. Do not choose/merge a winner; the user will compare all three and keep
one, with minor refinements as needed.
