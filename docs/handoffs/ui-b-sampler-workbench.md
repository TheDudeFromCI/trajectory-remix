# UI B — Sampler workbench / master-detail editor

Read [CONTEXT](CONTEXT.md), handoff 04 and contracts **§8–10**. Branch:
**`design/sampler-workbench`**. Same shared 03/04 backend as UI A/C.

## Design thesis

Optimize inspection and tuning of many dimensions with a searchable navigation
tree/table plus a readable detail pane. This should be efficient without turning
the matrix into an unexplained spreadsheet wall.

## Proposed implementation

- Dedicated Sampler Matrix tab; master navigation groups registry entries with
  clear labels, search and compact summaries. Details show description, choices,
  weights, conditional rules, constraints and metric linkage.
- Support precise numeric editing alongside understandable normalized/base-weight
  displays when the backend can actually derive them. Do not label base weights
  as observed output frequencies or invent exact joint probabilities.
- Keep advanced detail visible on demand; units/reasons beside affected controls.
  Preserve selection and scroll while editing; no full-pane rebuild per keystroke.
- A lightweight proposal preview uses the same fixed seed/pin API as other
  alternatives. Project edits versus per-run overrides are explicitly different.
- Unknown/custom matrix fields round-trip; no invisible deletion on save. Numeric
  drift tolerances remain per metric and editable through the shared definition.

## Shared constraints

No private backend/schema branch, sampler reimplementation, separate metric
registry or adaptive controller. Matrix edits affect future runs only. No real
LLM calls, tokenizer/compiler invocation or large distribution/throughput run.
The existing metrics panel remains the reporting surface.

## Review and correctness

Tiny functional tests: search/detail navigation, persistence, invalid constraints,
unknown fields, pins versus weights, fixed-seed preview and existing-run isolation.
Check mouse/keyboard edits and focus/selection without measuring throughput.

Deliver a draft UI PR with screenshots, a quick tuning workflow, tradeoffs and
user review points. Iterate as requested. Do not merge/declare this the winner;
user compares all three. Shared API fixes must be available to every prototype.
