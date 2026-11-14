# Stable common context

> **Temporary handoff material in draft PR #70. Remove `docs/handoffs/`,
> `docs/IMPLEMENTATION-CONTRACTS.md`, and `docs/ARCHITECTURE-OVERHAUL.md` before merge.**

Read this once per fresh task, then the assigned handoff and relevant contract
sections. The user approved the design; they requested handoffs to reduce
conversation/cache overhead. This file is temporary implementation-planning context.

## Authority and scope

Current user instructions and applicable repository instructions take precedence.
The packet does not authorize starting every work package or merging PRs.
Implement the assigned package, create a focused draft PR when requested by its
invocation, and leave unrelated work alone. No proactive subagents: the user's
three sampler-UI branches are design alternatives, not a request to spawn agents.

Use the approved [contracts](../IMPLEMENTATION-CONTRACTS.md) as the engineering
baseline. The [long register](../ARCHITECTURE-OVERHAUL.md) contains decision history;
consult only relevant sections when needed. Do not repeat product questions the
user already answered. Raise genuine contradictions with a concrete example.

## Hard validation boundary

**Never run throughput/load/scale benchmarks, full training-performance runs, or
live LLM acceptance here or in CI.** The user runs these on their own machine.
Target: **25k stored / 20 active**; real generation validation uses their local
LLM. Supply manual harnesses/instructions only. Small deterministic correctness
tests with a few mock requests or one optimizer update are allowed. Do not run
existing benchmark scripts just because they are already in the repo.

## Core source and compiler policy

- Dependency graph authoring; no legacy formats, migration, sandbox or tick anchors.
- One window per event. Same-window events must be ancestor ordered; broken
  references remain editable errors with one-click removal, not silent rewiring.
- Intrinsic interpretation/compilation data is primary. Optional metadata holds
  Remix stage/split/signoffs, targets/metrics, grouping and annotations. Removing
  metadata preserves compilation. Do not make workflow fields compiler inputs.
- No manual special tokens/placeholders. BOS/NOOP derived; Close window → EOS;
  headers → CTX; chat handler → MSG; independent tool/response begin/end flags.
- Framing inherits event source/mask/loss. Agent headers/calls stream; external
  headers/MSG are atomic; each tool-result event chooses instant or streamed.
- Tokenize each event independently. Whole-event loss/masking; responses masked
  by default. No token crosses an event boundary.
- External completion can enable same-tick generation; agent completion enables
  dependents next tick. Instant external chaining defaults on, with a checkbox
  for the next input tick. This is not arbitrary delay authoring.
- One BOS-only Available window, hidden from authored source/UI. First agent write
  activates it and creates another. External populated openings bypass it.
  Stagger coincident agent openings deterministically; never reopen a logical ID.
  Physical slots randomize/reuse; capacity counts peak Active, not Available.
- Other same-pass queries see pre-EOS contents but not current EOS. Close after
  emission; window absent next tick. Replacement removes old body and inserts
  first new token on the same tick, never emptying the window.
- All-Active NOOP sleeps; read-only counts as NOOP. Skip idle model ticks until
  context changes. Closed/replaced content must not leak back into actor context.
- Full training compilation/export only; explicit opt-in Tick history may reuse
  tokenization/scheduler read-only. Graph mode, generation/review/metrics never
  invoke token compilation. Preview cannot change source, metrics or export settings.
- Compatible tokenizer profiles/special maps live in Project Settings; reject
  missing controls without changing vocabulary. Preview selector is a client
  Preference; Export selector is independent.
- Errors block compilation; warnings do not. Tool input-schema mismatches warn;
  outputs are free-form, never schema-validated. No cross-event contamination or
  request-change reminder after edits; authors/reviewers handle consistency.

## Generation and review policy

- One shared proposal-driven Flow; Monte Carlo sampler + manual pinning. No
  adaptive balancing. Matrix/metric definitions linked; changes affect new runs,
  not historical targets or trajectories later in an already running run.
- Freeze run inputs/instructions until finished/cancelled. One generation call
  per trajectory at a time; parallelize across trajectories. One call → one event;
  Flow supplies IDs/deps/settings, LLM primarily supplies content.
- Independent FIFO per configured endpoint; each separate API call is an item.
  Transport retries stay in the current item until success/exhaustion. No manual
  priority or cache-affinity reordering. Independent endpoint/trajectory caps.
- Discard interrupted response text; regenerate that event from original inputs.
  Keep completed events/repair patches, no incomplete-response diagnostics.
- Retry exhaustion pauses endpoint/attempts, no quality demotion. Manual Resume
  only, no probes/fallback model; server restart also waits for manual Resume.
- N attempts → N distinct detached outcomes, pass/fail; retries do not count.
  Pause is not completion. No automatic replacement of failed/detached attempts.
- Attached pipelines are locked through reviews/repair; terminal completion or
  detach unlocks. Detachment is permanent; user-created sources start detached.
  Manual review/repair only on detached sources, with an exclusive operation lock.
- Stages: Unfinished -3 → Pending 0; structural retries exhausted → Unsuccessful -1.
  Fast pass/fail → Candidate 1 / Unsuccessful; Critical → Validated 2 / Rejected -2.
  Repair success: -1→1, -2→2; failure → detached Unfinished with edits retained.
  Repair is one continuing session until verdict/context limit, same tier model,
  spot patches, no duplicate same-model validation loop. Ready 3 is manual.
- Review JSON has per-checklist evidence/verdict/model/source/checklist identities.
  Edits stale signoffs, not stage; Core changes stale Candidate/Validated, Critical
  changes stale Validated. Re-review only manually. Metadata remains optional.
- Manual Review & Repair popup: standard/Force Fast/Force Critical, repair toggle,
  progress, closes on completion. Standard: Pending Fast only, Candidate Critical,
  normal failed-stage repairs, skip Validated/Ready. Force ignores stage, not locks;
  pass retains higher stage, failure normal transitions.

## UI, jobs, logs, exports

- Preserve overall UI; no sandbox/tick markers. Timeline graph default and Tick
  history option; scrub both; hover dependency indicators, searchable/direct links.
  Raw full-source editor stays advanced; tools keep Form/Raw JSON argument modes.
- Tick history refresh: **500 ms trailing debounce**, configurable in Preferences,
  resets on rapid edits; cache safely, reject stale results. Render automatic
  special tokens as bordered uppercase blocks, not authored strings.
- Dashboard uses REST throughout. MCP prioritizes agent readability over complete
  endpoint parity; provide focused task Skills/guides. Preferences/Undo stay local.
- Independent jobs/log instances; bottom job-status bar opens full per-job dialogs.
  Pause resumable; Cancel terminal, retaining commits and detaching started work
  after writers settle. No late publication. Unrelated jobs may continue.
- Bulk applies to frozen search matches across ALL pages: stage, split, missing
  metrics, delete, Review & Repair. Skip locked with warnings. One Undo action of
  actual changed-entry deltas after completion/cancel settles. Redo saved results,
  never model calls; protect intervening edits with revision checks.
- Log cache optional cap **1024 MiB** in Project Settings, oldest eviction,
  clear/export controls. Clearing logs never clears source/checkpoints/signoffs.
- All export shards/manifests/ZIPs/log downloads in **/tmp**, job-owned paths,
  partial cleanup and **24h** completed-artifact expiry. OS cleanup not guaranteed.
- Export minimum Ready default; include stale default on; raw JSONL or compiled
  KTREMIX; trajectories/file. Trim: remove all / measured metrics / all metrics.
  Carry forward keep-metrics default as all metrics. Required source survives.
- Split metadata explicit or Auto. Missing warns, automatic ratio-balanced export
  assignment by default; exclude-missing option off. Auto is not missing. Keep
  known families together; conflicting explicit family splits block export.
  Separate split files, resolve before trimming, don't rewrite source splits.
- Metrics unknown stay unavailable; explicit Calculate Missing Metrics. Record
  origin/source revision; per-metric tolerances, no universal 20%/1. Reuse metrics
  panel. LLM semantic tagging only a possible future feature, not confirmed scope.

## Durable memory and handoff hygiene

Follow the user's current durable-memory instructions and any newer instructions
in the fresh conversation. Save decisions and continuation state, not copied code
or credentials. Private workspace identifiers are deliberately omitted here.

This packet is temporarily tracked in draft PR #70 at the user's request. Remove
it before merge. Public implementation docs/Skills should describe the product,
not this conversation; do not carry the entire packet into follow-up PR diffs.
