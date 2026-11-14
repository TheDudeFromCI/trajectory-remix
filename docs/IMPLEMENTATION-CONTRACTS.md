# Trajectory Remix overhaul: implementation contracts and delivery plan

> **Temporary handoff material in draft PR #70. Remove `docs/handoffs/`,
> `docs/IMPLEMENTATION-CONTRACTS.md`, and `docs/ARCHITECTURE-OVERHAUL.md` before merge.**

Status: user-approved planning baseline. Implementation is now handed off to
fresh conversations via [the handoff index](handoffs/README.md); this conversation
only prepares that packet. Read with [the decision register](ARCHITECTURE-OVERHAUL.md).
Product decisions there take precedence. Names/defaults below are engineering
choices under delegation unless explicitly described as confirmed.

The existing public format/reference/demo work is draft PR #70. The user has
authorized temporary inclusion of this plan and handoffs there for fresh checkouts.
Remove the planning packet before merge; subsequent PRs should document implemented behavior.

## 1. Delivery and validation boundary

- Use focused draft PRs with small deterministic correctness checks.
- First usable milestone: manual graph authoring through REST, the retained UI,
  basic MCP/Skills, timeline modes, import/export and the real compiler wired to
  the training demo. Automated generation/review follows this core.
- No legacy readers, conversion tools, sandbox or tick-marker compatibility.
- Scale target: **25,000 stored trajectories / 20 active trajectory pipelines**.
- **Do not run throughput, load, scale or training-performance experiments in
  the assistant environment or CI.** Supply harnesses/instructions for the user
  to run on their own machine. Existing benchmark observations predate this rule.
- Real generation acceptance uses the user's **local LLM**, not a requirement
  for OpenRouter/hosted services. No live model calls are made while drafting.
- Small functional tests may use a few mock requests or one optimizer update;
  they must not become disguised throughput tests or timing-based CI gates.

## 2. Source boundary

Proposed format identifier: `kidra-trajectory-graph/1`. Raw JSONL contains one
complete source object per line. The compiler consumes intrinsic primary fields
plus an explicit export profile, never Remix workflow metadata.

Primary shape:

```json
{
  "format": "kidra-trajectory-graph/1",
  "id": "6ca0a6d8-c7aa-46b8-b931-bfcdf29e1d3b",
  "defaults": {"noop_loss_weight": 0.1},
  "windows": {
    "chat": {"purpose": "chatroom", "agent_writable": false},
    "thought": {
      "purpose": "reasoning", "agent_writable": true,
      "reasoning_effort": "Medium"
    }
  },
  "events": {
    "user-message": {
      "kind": "chat_message", "window": "chat", "after": [],
      "content": "Inspect the task and decide what to do next.",
      "chat": {"speaker": "Alice"},
      "delivery": {"mode": "instant", "chain_immediately": true},
      "supervision": {"masked": true, "weight": 1.0},
      "close_window": false
    },
    "think": {
      "kind": "agent_text", "window": "thought",
      "after": ["user-message"],
      "content": "I should check the task's current state first.",
      "supervision": {"masked": false, "weight": 1.0},
      "close_window": true
    }
  },
  "metadata": {"label": "Example", "stage": -3, "split": "Auto"}
}
```

`metadata` may be omitted entirely. Its removal must preserve compilation and
supervision for the same profile/seed. Stage, split, reviews, grouping/lineage,
sampler proposals, metrics and human annotations belong there, not in primary
fields. An app's workflow requirements do not make them compiler requirements.

Window/event map keys are stable opaque IDs scoped to this trajectory. New UI
entries can use UUIDs; human-readable imported IDs remain valid. Never use array
positions as API identities. Map/list presentation order does not introduce an
execution dependency. Missing dependency references are retained as draft errors
with a one-click remove-reference action. No silent rewiring/cascade deletion.

Primary optional fields are allowed when they affect compilation: effort labels,
delivery, supervision and framing flags remain primary even when defaults exist.
UI-only positions, colors and notes belong to client state or optional metadata.

### Event variants

| Kind | Origin/default supervision | Additional intrinsic fields |
|---|---|---|
| `agent_text` | Agent, unmasked | `content` |
| `external_text` | External, masked | `content`, optional replacement, instant/streamed delivery |
| `tool_call` | Agent, unmasked | `call_id`, `tool`, `insert_tool_begin`, `insert_tool_end` |
| `tool_response` | External, masked | `call_id`, response begin/end flags, delivery, optional replacement |
| `chat_message` | External chatroom insertion, masked | `chat.speaker`, `content`; handler supplies MSG framing |

All variants target exactly one window, have `after`, whole-event supervision
and optional `close_window`. Calls/fragments sharing a `call_id` must agree on
the tool/request window and be dependency ordered. Responses may target the
configured response window, not necessarily the request window. Outputs remain
free-form; no result schema. Form/Raw are editor representations, not separate
source kinds; Raw preserves exact argument fragments.

Framing flags are independent: begin-only first fragment, neither for a middle
fragment, end-only last fragment. A complete event can use both. Tool end submits
the assembled request; EOS before submission cancels it. End tool plus Close
window is legal: tool ending precedes EOS. No content follows EOS in that window.

Unsolicited startup/alarm/watcher inputs use `external_text`; they do not invent
an in-trajectory request. Explicit responses linked to a nonexistent/unsubmitted
call remain structurally invalid. Chat-tool auto-echo creates an ordinary linked
chatroom event; the agreed per-call opt-out allows manually authored outcomes.

Source `content` is literal data, not a parser for special-token placeholders.
Compiler/renderers insert logical controls from structure. Existing logical
window references, where used, should serialize as typed reference pieces
alongside text, not native slot numbers; all pieces retain the event's one mask.
Resolve reference pieces only against that export's allocation. Freeze their
small JSON grammar with the graph model PR rather than adding magic text syntax.

Replacement removes prior non-header content and inserts its first new token in
the same tick; subsequent streaming follows delivery settings. Preserve window
identity/slot and Active status. Empty replacement is a compilation error.
Use external/tool-observation replacement for runtime document updates; editing
the wording of an older event is a source edit, not a new runtime replacement.

### Diagnostics

Separate DTO/parsing failures from editable-source diagnostics. Malformed JSON,
wrong API argument types and unsupported format versions reject the request.
Syntactically well-formed graph drafts may retain broken references/cycles and
return diagnostics; they cannot compile until errors are fixed.

Errors include invalid graph references/cycles, unordered same-window writers,
invalid lifecycle/framing and profile/control incompatibility. Warnings include
submitted tool-argument/schema nonconformance, drift and unacknowledged open
reasoning/tool windows. Errors block compilation; warnings do not. No semantic
contamination/reminder propagates to other events after an edit. Saved review
signoffs are separately marked stale under the confirmed policy.

## 3. Project versus client settings

Project records: tool input schemas; tokenizer profiles; matrix/metric registry;
split ratios; reasoning-effort choices; Core/Critical checklists; configured
Generator/Fast/Critical endpoints and concurrency/retry settings; log-cache cap.

Client Preferences: graph/tick-history mode, preview profile ID, preview debounce
(500 ms), editor/display state and bounded Undo action count. Preferences neither
write project configuration nor change export selection. No token-insertion mode.

Tokenizer profiles have stable IDs, names, tokenizer definitions, special-token
maps and fingerprints. Reject missing required dedicated tokens; never extend
vocabulary automatically. No tokenizer is required for Graph authoring, review,
generation or raw export. The opt-in Tick history profile is independent of the
compiled Export dropdown. Source never adopts native tokenizer spellings.

New-project numerical defaults not explicitly chosen by the user are engineering
defaults to document in the relevant PR: conservative provider concurrency,
bounded retry counts and finite memory caches. Avoid a universal drift default;
store tolerances per metric and explain their units.

## 4. Compiler and visual scheduler

Use one scheduling core. Full export emits training arrays; Tick history requests
reuse tokenization/scheduling read-only and never write source, metrics or files.
Graph rendering/review/generation/metric refresh must not invoke token scheduling.

Schedule contract:

1. Validate the DAG; tokenize each event independently, including generated
   framing with that event's source/mask/loss. No token crosses event boundaries.
2. Resolve roots and dependency completion. External input completing in tick N
   can enable generation in N; agent completion enables dependents in N+1.
3. External instant chains coalesce in the input phase by default. The receiving
   event's checkbox can defer it to the next input tick. This is distinct from
   instant/streamed content delivery, and not an arbitrary delay facility.
4. Agent headers/CTX/tool requests stream one emitted token per window/tick.
   External headers are atomic before first content; chat MSG insertions are
   atomic. Tool results follow each partial event's instant/streamed mode.
5. Preserve Progressive Window Assignment: one BOS-only Available; activation
   replaces it immediately. Agent opening collisions are staggered deterministically;
   external openings are exempt. Logical IDs never reopen; vacant physical slots
   may be reused under the seeded allocator. Capacity counts peak Active only.
6. Same-pass predictions see pre-existing window content, never the current
   generated token. EOS closes immediately after emission and is never an input
   key. The window is absent next tick. Clear context on physical-slot reuse.
7. All-Active NOOP triggers sleep; read-only windows count as NOOP. Retain one
   decision frame and skip idle model ticks until an external context change.

Exports pin source revisions, profile/seed and compiler versions. Preview uses a
stable cache key including intrinsic source hash/profile/seed, with stale-result
rejection. A trailing 500 ms configurable timer coalesces edits while Tick history
is active; no per-keystroke scheduling or Graph-mode tokenization.

The new compiler must emit the existing draft KTREMIX frontier-array contract,
including explicit visibility, replacement intervals, instance/slot lifetimes,
weights and target rows. Strengthen format conformance where the settled EOS and
phase rules require it. Keep EOS as a label, not a retained KV token.

## 5. Persistence, edits and ownership

Use the existing FastAPI/SQLite/WAL stack, without an old-project migrator. Store
new-format project/version identity. Proposed logical tables:

| Record | Important keys/data |
|---|---|
| `trajectories` | UUID, current revision/hash, primary source + optional metadata |
| `trajectory_revisions` | immutable revision identity, source/annotation changes and provenance |
| `tool_usage` | indexed tool ID → trajectory IDs/source revision |
| `runs` | immutable config/prompt/matrix/checklist snapshot, attempt quota/seed/state |
| `attempts` | run ID, ordinal, trajectory ID, proposal digest, terminal outcome |
| `jobs` | kind/state, owner epoch, progress, parent/run/resource references |
| `request_items` | endpoint ID, FIFO sequence, job/step ID, retry count/status |
| `step_commits` | idempotency key, accepted event/review/repair patch, checkpoint |
| `job_logs` | per-job cursor/segment references and retained-size accounting |
| `artifacts` | job ID, `/tmp` path, size/checksum, completion/expiry/download state |
| `project_changes` | bounded cursor-indexed invalidations, not full source copies |

Keep source identity, annotation/review revisions and job progress distinct.
API edits require a per-trajectory expected revision. On conflict return current
revision plus a bounded diff/reference; preserve the browser draft for explicit
reconciliation. Never overwrite it because another trajectory changed.

Commit accepted content, checkpoint, ownership transition and notification in one
short transaction. Never hold transactions during provider requests or expensive
scheduling. Request commit idempotency does not promise exactly-once provider
billing after an ambiguous timeout.

Attachment is internal operation ownership, not a compiler input. Generation
owns its trajectory through all enabled reviews/repairs. Completion, manual
detachment, failed-repair abandonment or terminal generation cancellation detaches
under the agreed rules. Detached trajectories never reattach. Manual operations
temporarily lock detached trajectories and cannot bypass another owner.

## 6. Request queues and lifecycle

Each logical configured endpoint has an independent FIFO queue and concurrency
cap. Matching URL strings alone do not merge endpoint identities. Separate
generation/review/repair tool-turn requests are separate items; transport retries
stay in their current item until success/exhaustion. No interactive/cache-affinity
priority. Parallelize across trajectories, initially one generation call per
trajectory; review and repair share one exclusive operation slot per trajectory.

A run pins its instructions until completion/cancellation, including trajectories
started later. Matrix/Flow edits affect new runs only. Credentials stay outside
source/snapshots. A request interruption discards incomplete response text; retain
completed events/repair patches, not partial-text diagnostics. Restore the original
inputs when retrying, without silently resampling the proposal.

Endpoint retry exhaustion pauses it and preserves affected attempts/stages.
Other endpoints continue. Manual Resume is required; no background recovery probe
or automatic endpoint substitution. After server restart, interrupted jobs also
wait for manual Resume. Pause retains attachment; Cancel is terminal and detaches
started unfinished attempts after writers settle, retaining committed results
without a quality-failure verdict. Fence late results from cancelled/detached work.

N requested attempts means N distinct trajectory lifecycles, not requests/passes.
Retries stay within the attempt. Only detachment removes it from the active pool;
a normally completed N-attempt run retains N detached outcomes, pass or fail.

## 7. Review, repair and Undo

Stages remain optional metadata: Unfinished -3, Rejected -2, Unsuccessful -1,
Pending 0, Candidate 1, Validated 2, Ready 3.

Automatic generation: Unfinished → Pending; exhausted structural retries →
Unsuccessful. Enabled Fast pass/fail → Candidate/Unsuccessful; enabled Critical
after Fast pass → Validated/Rejected. Completion detaches. Ready is a manual stage.

Manual Review & Repair popup: standard/Force Fast/Force Critical and repair toggle.
Standard reviews Pending with Fast only and Candidate with Critical; skips
Validated/Ready and uses normal failed-stage repair rules. Forced tiers ignore
stage restrictions, not locks. On success retain the higher stage; failures use
normal transitions. A lower-tier pass does not create a higher-tier signoff.

Repair is one continuing session with the corresponding review model, spot
patches preferred, until verdict/context exhaustion. Unsuccessful success →
Candidate; Rejected success → Validated; failure → detached Unfinished, keeping
edits. No separate same-model revalidation loop. No resetting a repair's context
limit through silent new sessions. Backend outages pause instead of quality failure.

Signoffs are metadata: per-item JSON evidence/verdict, model identity, source and
checklist hashes. Source edits stale reviews without changing stage; Core edits
stale Candidate/Validated, Critical-only edits stale Validated. Re-review is manual.

Bulk targets a frozen search result across all pages. Actions: stage, split,
calculate missing metrics, delete, Review & Repair. Skip locked entries with
warnings. One browser-cache Undo action contains only changed-entry deltas;
enable after completion/cancellation settles. Redo restores saved results, never
calls models. Revision/lock checks prevent overwriting intervening edits.

## 8. Sampler, metrics and dataset splits

Integrate the supplied `sample_one` implementation/configuration, not a rewritten
approximation. Recorded source identities/version/API are in the decision register.
`definition_id` is provenance, not a storage key: use a UUID and full payload digest.
Preserve seed, overrides, matrix/version/digest, proposal and requested targets
before requesting content. Retry/resume reuses them. Monte Carlo plus manual
pinning remains the policy; adaptive coverage balancing is deferred.

One shared Flow handles user-driven, multi-user and autonomous phases, including
switches within a trajectory. One content-generation call produces one event.
Flow supplies IDs, dependencies, types/flags and minimal role-specific instructions;
the model supplies content. Scope actor context to available causal evidence,
excluding future/parallel/closed/replaced text. Privileged world/review context
must not enter agent prompts. Tool-output construction needs the planned local-LLM
acceptance cases, not claims based only on mocked responses.

The provided sampler artifacts were inspected earlier and hashes recorded, but
their physical copies are not currently found in this workspace. Restore the
exact supplied files before coding/verifying the adapter; do not recreate their
43 behaviors/22 axes/constraints from a summary. This is an implementation input
dependency, not an invitation to redesign the agreed sampler.

Metric records are optional metadata. Proposed entry: definition ID/version,
value, origin (`source_calculation`, `manual_annotation`, `export`), source revision,
and profile/compiler identity when relevant. Unknown stays unavailable. Calculate
Missing Metrics fills determinable unknowns explicitly; no automatic LLM tagging.
An LLM semantic-tagging helper remains only a possible future idea.

Share the metric registry with the sampler matrix; editing it updates definitions
for future runs, never old sampled targets. Keep targets and actual values apart.
Per-metric tolerances use relative/absolute bands with units; no universal 20%/1
default. Reuse the existing metrics panel and its query/population support.

Split mode is explicit Train/Development/Test or Auto; absent split warns. By
default export resolves Auto/missing values using project ratios and existing
explicit assignments. The missing-split exclusion option defaults off and does
not exclude intentional Auto. Known same-scenario variants share a family; do
not group unrelated cases just by domain/template/run seed. Conflicting explicit
splits within a known family block export. Keep groups together before balancing;
ratios can be approximate when indivisible families prevent exact balance.

Resolve splits against an export snapshot, preserve project metadata, partition
into split-specific files, then trim metadata/shard. Preserve resolved split in
file/manifest identity even when record metadata is removed.

## 9. REST and agent interface

Use one application command/query layer behind all transports. The dashboard
uses REST for project/job operations. MCP provides readable composable tasks,
not necessarily one tool per route. Proposed `/api/v1` resource surface:

| Area | Operations |
|---|---|
| Project/settings | inspect project; versioned settings changes; tools/tokenizer/checklist/matrix registries |
| Trajectories | paginated query; create/get; revision-checked source/metadata patch; delete |
| Events/dependencies | add/update/delete by stable ID; add/remove references; diagnostics |
| Views | graph/window history; read-only Tick history requests with profile/source hash |
| Runs | create N-attempt run with pins/config snapshot; inspect attempts/outcomes |
| Jobs | paginated inspect; progress; pause/resume/cancel/detach where eligible |
| Bulk | freeze query/action/config; start background operation; report skipped/conflicts |
| Metrics | population/query; definitions; explicit missing-value calculation |
| Review & Repair | mode/toggle; source revision/ownership guard; return job reference |
| Export/import | background job; stage/split/trim/shard/profile options; artifact references |
| Logs | per-job cursor reads; export retained snapshot; cache usage/settings/clear |
| Change feed | cursor/revision invalidations; bounded reconnect/full-resync fallback |

Successful commands return operation/revision IDs, changed IDs and diagnostics;
long work returns `202` plus a job ID/status link, not its entire eventual output.
Use `409` for revision/ownership conflicts, structured field errors for invalid
DTOs, and stable source diagnostic codes/pointers. Source errors do not become
generic HTTP 500s. Preserve client drafts and skip-report bulk conflicts.

Supply focused Skill/guide files: setup, graph edits, dependencies/windows,
tools/chat, reviews/repair, jobs/recovery, bulk operations, metrics/search and
export/training. Update MCP discovery/examples with each capability. Do not keep
legacy sandbox/tick-marker lessons as if they describe the new workflow.

## 10. UI, jobs, logs and artifacts

Keep the overall existing UI. Remove sandbox and authored tick markers; retain
optional Source JSON and timeline scrubbing. Default graph mode plus opt-in Tick
history use the existing timeline area. Hover dependencies replace marker hints.
Support searchable dependency selection and direct linking in both modes.
Token blocks render automatically from source structure, with uppercase labels.

Any number of non-conflicting jobs can be managed; resource ownership/worker caps
still apply. Bottom status indicators open per-job full inspection dialogs. Each
job has separate logs/state/progress/controls. Job changes never steal selection.
Do not keep one global active-job object in the client. Review & Repair popup
closes at completion, while warnings/results remain inspectable in job history.

Log cache cap is optional, default 1024 MiB in Project Settings, with oldest-first
eviction, Clear Log Cache and export. Page/segment per-job logs; clearing/evicting
log text never removes source, checkpoints, owners, signoffs or Undo. Running
jobs can append after a clear. Mark truncated/evicted history honestly.

All export shards/manifests/ZIPs/log-download artifacts are job-owned temporary
files in `/tmp`. Publish only complete downloads. Clean partials after failure/
cancellation settles, and completed files after 24 hours; check expiry on startup
and periodically while running. OS removal may happen earlier. Do not delete
active writer/download files. Keep compact completed-job state independently of
artifact/cache expiry. ZIP packaging does not change KTREMIX file semantics.

Export options: minimum stage Ready (or Validated/Candidate/Pending), include
stale default on, raw JSONL versus compiled KTREMIX, trajectories per shard,
tokenizer dropdown for compiled output, exclude missing split default off, and
metadata projection. Projection choices: remove all; measured metrics only;
all metrics (targets + measured), carrying forward the keep-metrics default.
Disabling trimming keeps full optional metadata. Compiler inputs never disappear.

## 11. Focused PR boundaries

1. **Graph/contracts foundation:** typed source, diagnostics, new project/settings
   schema, revision-guarded edits and small fixture corpus. No legacy adapters.
2. **Compiler/export core:** framing, dependency scheduler, allocation/loss/visibility,
   KTREMIX emission, split-aware temporary shards and artifact jobs. Adapt the
   training demo to consume real new-compiler output, not the legacy fixture adapter.
3. **Manual studio milestone:** retained UI through REST, graph/tick-history views,
   tool forms/raw, dependencies, project tokenizers/settings, job status/dialogs,
   source import/export and essential MCP/Skills. First coherent usable milestone.
4. **Sampler/metrics integration:** restore exact input artifacts; registry/proposal
   adapter, pinning, immutable run inputs, coverage/actual provenance and families.
   Run the three matrix-UI design branches against this same contract; user picks
   the winner after iterations, without diverging backend implementations.
5. **Durable FIFO/recovery/bulk:** independent endpoint queues/caps, lifecycle/locks,
   crash/manual resume, cancellation, search-wide bulk, delta Undo/Redo and log cache.
   Its backend primitives precede the completed manual-studio job UI and all real
   parallel generation. It can be developed after 01 while 02 progresses.
6. **Generation/review orchestration:** one shared branching Flow, content-only
   event requests, local/OpenAI-compatible endpoint capability handling, JSON
   reviews and continuing spot repairs. Requires 04/05; mock correctness first.
7. **Acceptance/docs/cleanup:** user-run local-LLM suite and 25k/20 scale harness,
   remaining MCP/Skills/docs, actual new-compiler training demo verification and
   removal of obsolete code. Do not run throughput locally in the assistant or CI.

These are work-package labels, not required chronological numbering or GitHub
PR numbers. Critical path is 01 → 02 + 05 → 03 for the first manual milestone;
06 waits for 04/05. Adjust small slices to keep each PR runnable. Implement only
the handoff assigned in a fresh task; this packet does not instruct starting all
work or merging anything. No production code was changed during packet creation.

## 12. Readiness checklist

- [x] User reviewed/approved this planning baseline.
- [ ] Assigned foundation task freezes graph/protocol/profile field grammar.
- [ ] Restore the exact supplied sampler files before sampler-adapter work.
- [ ] Fixtures cover DAG errors, phases, framing, EOS, reserve, reuse/replacement,
  loss boundaries, split groups, stage/signoff logic and metadata-free compilation.
- [ ] Mock request tests cover FIFO/retries, frozen run inputs, no partial-text
  artifacts, idempotent commits, cancellation/detach fencing and manual restart.
- [ ] UI correctness covers REST-only operations, both scrub modes, 500 ms trailing
  preview coalescing, no stale results, full-search bulk and changed-only Undo.
- [ ] Export tests check required array semantics, CRC/bounds, raw/compiled shards,
  split separation, metadata projection and temporary-file lifecycle.
- [ ] Prepare user-machine harness/instructions for 25k stored/20 active; no automatic
  CI/assistant throughput execution or machine-specific speed guarantees.
- [ ] User runs local-LLM chat/multi-user/wakeup/tool/review/repair smoke scenarios
  and inspects the samples; report failures without concealing failed attempts.
- [ ] User runs the real-compiler training demo and performance/load checks.

No unresolved product question is manufactured for routine field names. Genuine
contradictions discovered while finalizing schemas are recorded with a concrete
example for focused review before implementation relies on them.
