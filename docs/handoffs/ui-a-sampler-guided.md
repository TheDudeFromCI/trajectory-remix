# UI A — Guided sampler-matrix editor

Read [CONTEXT](CONTEXT.md), handoff 04 and contracts **§8–10**. Branch:
**`design/sampler-guided`**. Requires the shared 03/04 UI/API backend. This is one
of THREE alternatives requested by the user, not authorization to choose a winner.

## Design thesis

Make the dense matrix readable through named sections and progressive disclosure.
Use clear descriptions, grouped form controls, summaries and search. A newcomer
should understand a competency/setting before encountering its numeric weights.

## Proposed implementation

- Dedicated Sampler Matrix tab in Generation Settings, using enough width/height
  for readability while preserving the rest of the existing application.
- Sections derived from actual matrix/registry: competency focus, scenario/actors,
  tools/resources, conditions/constraints and advanced weighting as applicable.
- Meaningful labels, field help, units, per-metric tolerances and eligibility
  explanations. Collapsed sections summarize important choices.
- Separate project matrix edits from per-run pins. Default pin is Any/unpinned.
  Edits update shared definitions/metrics for FUTURE runs, not historical targets.
- Small deterministic proposal preview with fixed seed controls, showing what
  changed without pretending target values are measured trajectory metrics.
- Preserve unknown/custom fields through edits. Typed advanced controls rather
  than forcing raw JSON as the primary interface.

## Shared constraints

No backend/schema/Monte Carlo changes unique to this branch. Same API, fixtures,
source persistence and generation behavior as UI B/C. No live model call, auto
sampling controller or tokenization/compiled metrics in the matrix editor.
Don't redesign the rest of the app or make full-dataset queries on every keystroke.

## Review and correctness

Use tiny fixtures: load/save round trip including unknown fields, pins versus
weights, validation/eligibility messages, metric linkage, future-run snapshot
isolation, keyboard/focus behavior and simple search/expand navigation. Screenshots
can use the tiny fixture; no throughput/large proposal distribution test.

Deliver a draft UI PR with screenshots, a short navigation walkthrough, pros/cons
and concrete questions for user review. Expect multiple revisions/experiments.
Do not merge or discard the other alternatives. Record any shared API fix as a
separate prerequisite change available to all three branches.
