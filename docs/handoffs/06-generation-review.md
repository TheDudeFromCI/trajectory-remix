# 06 — One shared event-generation Flow, reviews and spot repairs

Read [CONTEXT](CONTEXT.md), contracts **§6–8**, and targeted sampler/review/context
sections of the register. Requires 01/04/05; do not bolt parallel generation onto
the old global session. Suggested branch: `feat/event-generation-review`.

## Inspect first

`builder.py`, `model_session.py`, Flow-related modules/templates, `generation_*`,
`trajectory_review.py`, `structured_output.py`, endpoint/settings and request
tests. Exact file locations may change in prerequisites; inspect current code.

## Scope

- One proposal-driven branching Flow, not one flow per competency. Handle ordinary
  chat, multiple users, external wakeups and within-trajectory state transitions.
- Monte Carlo/manual pins from 04, immutable proposal/run inputs. Each generation
  call produces ONE event. Flow wraps content with IDs, dependencies, flags,
  window settings and loss, minimizing LLM bookkeeping and boilerplate.
- Model-visible actor context uses available causal evidence only; no parallel/
  future/closed/replaced text. Privileged world/review info must not enter agent
  prompts. Reviewers assess causality, not evidence that arrived later.
- Automatic chat echo handler and per-call opt-out, structured MSG generation,
  user/agent chat through the read-only chatroom/tool arrangement. External
  startup/alarm/watcher context must not need a fabricated in-sample tool request.
- Tools use project input schemas, raw/form argument fragments, fully authored
  free-form responses, begin/end/closure flags, no sandbox/callback execution.
  Test response construction rather than claiming schema-valid text is coherent.
- FIFO 05 request service, one generation call per trajectory; parallel across
  trajectories. Cache-friendly stable prefixes, never queue reordering.
- Three configured endpoints Generator/Fast/Critical; repair uses its review model.
  Automatic reviews/repair default enabled when configured; no spending-cap UI.
- Stage transitions/checklist JSON/evidence/provenance, review staleness/manual-only
  re-review, continuing single repair session and archive-on-failure as in CONTEXT.
- Manual Review & Repair popup driver: stage-based versus forced modes, repair
  toggle, progress, complete/skip counts, no automatic reattachment or hidden
  additional review tier. Forced success retains higher stage; failure normal.
- Distinguish invalid provider output/API failure from a negative quality verdict.
  Use bounded format correction where appropriate; never invent a pass/fail merely
  because transport/JSON failed. Persist actual verdict evidence/version identity.

Do not add an LLM semantic-metric tagger or adaptive sampler. Do not auto-flag
other events contaminated after edits. No tokenizer/compiler in actor generation,
review or repair; Tick history is a separate read-only UI request.

## Small offline acceptance

Scripted fake responses cover a tiny ordinary/multi-user/wakeup sequence, partial
request with intervening information retrieval, instant/streamed result variants,
review pass/fail JSON and malformed output, repair success/failure/context limit,
frozen settings, actor-context capture and stage/ownership transitions.
Do not start a real local or hosted model here, and do not run throughput tests.

## User-machine validation deliverable

Prepare a small LOCAL-LLM smoke suite/config instructions for the above scenarios,
with reportable outcomes and sample inspection. No hidden required OpenRouter
dependency, CI invocation, backend spending controls or automatic replacement of
failed attempts. User configures endpoints/caps and runs it on their computer.

Deliver a focused draft PR with deterministic evidence, exact manual/local checks
not run, and any prompt/tool-response limitations needing local iteration. No merge.
