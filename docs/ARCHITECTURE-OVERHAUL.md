# Trajectory Remix architecture overhaul — discussion draft

> **Temporary handoff material in draft PR #70. Remove `docs/handoffs/`,
> `docs/IMPLEMENTATION-CONTRACTS.md`, and `docs/ARCHITECTURE-OVERHAUL.md` before merge.**

Status: user approved the planning baseline, then requested handoffs for the
remaining PRs instead of further implementation in this long conversation.
Only the public format/reference/demo PR has implementation changes so far.
Repository baseline: `97a47c2` (inspected November 12, 2026).
Maintain this document and the discussion register as decisions are made. A
proposal below is not an adopted decision unless explicitly marked confirmed.

Consolidated engineering contracts and focused PR plan:
[IMPLEMENTATION-CONTRACTS.md](IMPLEMENTATION-CONTRACTS.md). The user approved
this baseline. [Handoffs](handoffs/README.md) scope the next fresh tasks. Exact
sampler attachments must be restored before sampler-adapter work; hashes and the
earlier verification are recorded below, but the files are not currently present.

## Confirmed requirements

- Replace tick-based authoring with a dependency graph and execution-block timeline.
- Preserve the existing compiled token-tick execution, NOOP, attention and export
  semantics. This is an authoring redesign, not a new training architecture.
- Full training artifact compilation occurs during export. Graph-mode editing,
  generation, review, source metrics and tool checks remain tokenizer-free.
  The later user-requested opt-in Tick history preview may tokenize/run shared
  scheduling logic for visualization only, without changing source/export state.
- Make authoring independent of the export tokenizer.
- Integrate the new structured sampler instead of reducing its output to tags;
  preserve its metadata through generation, repair and export.
- Support concurrent sample generation through hosted models, including potential
  OpenRouter use, and dozens of simultaneously changing trajectories.
- Design discussion is complete enough for the user-approved planning baseline;
  continue implementation through assigned fresh handoffs. Keep genuine remaining
  engineering/validation issues visible without reopening settled policy.
- Use larger numbered question batches grouped by subsystem (updated preference).
- Events wait for all direct dependencies to finish and then execute immediately;
  events with no dependencies execute at trajectory start.
- Each event targets exactly one window. Events targeting the same window must
  be comparable by ancestry; unordered same-window events are potential collisions.
- Dependencies reference complete events. Represent partial observations with
  separate events rather than positions within a larger block.
- Progressive Window Assignment is required: exactly one empty writable
  Available window exists at any time. The agent's first write activates it and
  immediately creates a new Available window. Active windows contain text.
- External tools may create a window directly with content in the Active state,
  bypassing Available. EOS still closes windows. Hide the empty Available window
  in the dashboard.
- Every authored window has a unique stable ID and opens on the first frame in
  which text is written to it. Available windows are omitted from trajectory
  source and inserted automatically during export compilation.
- Simultaneous agent-created window openings are allocation collisions:
  compilation orders them deterministically and pushes an opening back by one
  tick, propagating dependency delays as needed. External-created windows are
  exempt. This does not relax same-window ancestry rules.
- Authored window IDs cannot be reused after EOS. Compiled physical slots are
  randomized and may be reused when vacant. Window capacity measures peak
  simultaneously open Active windows, excluding Available, not total distinct
  authored windows over the trajectory.
- An Available window contains BOS conditioning only, with no header/text, and
  predicts NOOP each idle tick with the usual NOOP loss. BOS alone does not count
  as text activation. These compiler-inserted windows remain hidden in authoring.
- A tool starts only after its complete request is submitted. EOS before
  submission abandons the unfinished call; it must not start or emit results.
- Submission and closure are event flags shown as `end-tool` and `close-window`
  checkboxes. No separate submit/close event or text scan is required. The final
  request fragment can complete text and submit the call in one event. Render
  protocol endings from flags; EOS is always a window's last token.
- EOS also cancels remaining tool output after submission; retain earlier
  observations and permit no tokens after closure in that window.
- All special-token framing is automatic from structural fields/handlers. No
  manual insertion, text placeholders or insertion-mode preference is required.
- Tool calls expose independent Insert tool begin / Insert tool end checkboxes;
  tool responses expose independent Insert tool response begin / end checkboxes.
  Unchecked endings allow fragments to continue later. Close window supplies
  EOS; BOS/NOOP, header CTX and chat MSG framing are handled automatically.
- Separate Preferences from Project Settings. Preferences are client-side and
  do not modify project configuration; project-affecting settings belong in the
  project settings section/panel.
- Remove executable sandbox simulation from the new graph workflow. Tool outputs
  are authored observations. Tools are premade/project-defined JSON schemas,
  editable through forms in Project Settings; users can create custom tools.
- Tool schema mismatches produce warnings, not authoring blockers. Tool events
  have schema-driven Form and Raw JSON modes. Raw mode supports partial requests.
- Tool schemas describe inputs only. Outputs are always free-form; do not add
  optional output schemas or output-schema validation. Preserve author flexibility.
- Tool results are series of events, instant by default. Each successive result
  event depends on the preceding one by default and may add dependencies on
  external events. Each result event independently selects instant or streamed
  delivery; a call can mix both modes across its result events.
- Streamed result events have configurable delivery rates. Dependencies are the
  only authoring timing controls: no explicit delay fields or wait events. Ticks
  arise only during compilation; insertion rates and required window-opening
  staggering are export scheduling details, not authored start-time offsets.
- If all Active windows emit NOOP on the same tick, the model sleeps. Read-only
  windows (documents/system prompt, etc.) are always treated as NOOP. Sleep ends
  when context changes: new text, a new window, replaced content, etc. Only one
  model tick passes; do not emit repeated model ticks through the sleeping gap.
- Tool schema edits recheck only trajectories using that tool against the current
  project definition. Preserve their source unchanged and publish advisory
  mismatch warnings; never scan every trajectory just to find affected users.
- Generation is proposal-driven, with matrix edits defining new competencies and
  scenario variation rather than requiring a separate authored Flow for each.
  One shared Flow still implements LLM generation, with branches controlled by
  proposal parameters. Matrix parameterization does not replace implementation.
- Multi-user and idle-heartbeat support are required; their implementation needs
  further discussion. Default instruction/response uses one shared chatroom for
  single or multiple users. The model cannot write directly into that window;
  it submits messages through the chat tool, avoiding model/user write overlap.
- Chat calls automatically create their chatroom entry by default, with an
  opt-out checkbox for custom outcomes (for example censored text or a spam
  rejection). Turning automatic handling off allows explicit authored results,
  including no chatroom entry, rather than forcing an echo of requested content.
- Idle heartbeat means stimulus-driven autonomous work without a new user
  instruction: startup, alarms, file watchers or other external observations wake
  the model, which reacts using standing instructions and current task state.
  This is not necessarily a generic periodic heartbeat message or a user chat.
- A single trajectory must support transitions between user-driven and autonomous
  phases. User rationale: the model must learn to process these changes during
  inference, not only handle isolated samples with fixed interaction modes.
- Windows can remain open at trajectory end. Warn by default for open reasoning
  or tool windows, unless the author explicitly marks that individual window as
  intentionally left open (for example a long-running tool). Do not auto-close
  windows or require full-project completion at a sample boundary.
- Reasoning-window headers include selectable reasoning effort. Initial levels
  are Low, Medium and High; projects can configure the levels. Show a dropdown
  when opening/initializing a reasoning window (first-write authoring interface).
- Sampled numeric targets are advisory, not exact acceptance requirements. Warn
  if realized trajectory counts drift substantially from targets. Explicit hard
  project limits remain separate; generators need not match exact turn counts.
- Drift-warning tolerance is configurable per metric in Project Settings, using
  a percentage band and a minimum absolute difference to avoid noisy warnings
  on small targets. Default values remain a design-review item.
- Matrix editing lives in a dedicated Sampler Matrix tab in Generation Settings.
  Readability and information organization are first-class requirements. The
  user finds the current matrix too dense to understand and is willing to devote
  a whole PR to refining this interface; plan a dedicated usability work item.
- Matrix-tab UI will be explored on three separate design branches. The user
  delegates implementation-design choices, expects repeated review/experimentation,
  and will select the best design for final tweaks. Do not finalize one layout
  before comparative review; implementation still begins after design discussion.
- Individual generation runs support sampler overrides: pin selected choices
  while all remaining fields are sampled. Overrides are per-run inputs and saved
  proposal provenance, not implicit edits to the project's matrix.
- Generate trajectories in chunks, not an entire graph in one response or a
  size-based whole-graph hybrid. Build each agent-writing prompt from its graph
  causal frontier: accessible open-window text only, no parallel/future text and
  no closed-window contents. User rationale: avoid oracle knowledge and produce
  more realistic reasoning traces.
- The shared Flow is the oracle: it holds full proposal/world state, chooses
  generation tasks and supplies scoped nudges. Every LLM gets only enough
  information for its corresponding event(s), not a complete answer/future trace.
- Each event-authoring LLM call generates exactly one event. Consecutive events
  use separate calls, each with its proper causal view. Do not implement multi-
  event/parallel-aware response generation for speculative rare edge cases.
- Minimize LLM workload: for reasoning and user messages, request text only and
  construct the full event programmatically. Flow supplies identifiers, targets,
  dependencies, headers/markers and settings. Tool-result construction is less
  settled and requires representative testing before choosing its output contract.
- Structural content failures use bounded event-local retries, then pause only
  that trajectory while other jobs continue. Advisory tool-schema/target warnings
  do not trigger retries. Deterministic wrapper bugs are not repaired by asking
  the LLM to regenerate otherwise valid text.
- Generated trajectories start Unfinished and become Pending on completion.
  Structural trajectory failure immediately sets Unsuccessful. Optional Fast
  Review uses a mid-tier model/checklist: pass Candidate, fail Unsuccessful.
  Optional Critical Review runs only after Fast Review succeeds: pass Validated,
  fail Rejected. Attempt Repair uses mid-tier for Unsuccessful, high-tier for
  Rejected. Structural trajectory failure occurs after bounded event retries
  are exhausted, not at the first invalid event attempt.
- Attempt Repair is a combined Repair & Validate step using the corresponding
  validation model. Success: Unsuccessful -> Candidate, Rejected -> Validated.
  Failure from either eligible starting stage -> Unfinished. Do not add a separate
  same-model re-review loop or reset successful repairs to Pending.
- Failed repairs retain their edit attempts at Unfinished, with prior revisions
  preserved. This Unfinished state is primarily an archive for substantial manual
  rework or explicit delete/recreate, not ordinary automatic resumption/reuse.
- The generation pipeline has exactly three user-added endpoint roles: Generator,
  Fast Review and Critical Review. Repair uses its corresponding review endpoint;
  no fourth repair endpoint. All reviews and repairs support manual triggering
  as well as automatic mode. Automatic mode defaults on when the required
  endpoints are available; adding them is the user's implicit agreement to use
  them automatically. Existing off toggles must remain meaningful.
- Repair & Validate gets one attempt only, continuing until a model verdict or
  context-limit exhaustion. Hitting the context limit is failure: retain edits at
  archived Unfinished. No automatic second attempt; prioritize credits for new
  trajectories and leave hard repairs to humans.
- Repair uses a continuing graph-inspection/targeted-edit session. Spot repairs
  are preferred; preserve unchanged events, IDs, history and provenance rather
  than regenerate the full trajectory. The single attempt ends at its verdict
  or context limit as already specified.
- Fast and Critical Review share a core checklist; Critical adds deeper checks.
  Corresponding Repair & Validate sessions use the same tier's checklist. Default
  wording is editable project-wide for both Core and Critical criteria. Review
  results must be JSON with every applicable checklist item and evidence for its
  pass/fail, saved as a signoff in optional Remix metadata with the reviewing
  model's identity. The user's later clarification explicitly keeps review
  records out of the intrinsic trajectory/compilation schema.
- Manual edits to reviewed Candidate/Validated trajectories mark reviews stale
  without changing stages or historical signoffs. Core checklist changes stale
  Candidate and Validated; Critical-only checklist changes stale Validated only.
- Stale trajectories always wait for manually triggered re-review. No automatic
  refresh option, including when normal post-generation review is enabled or
  review endpoints are available. Source/checklist edits must not spend credits.
- Lock a trajectory throughout its attached automatic pipeline, including
  reviews/repairs. Editing is allowed only when the pipeline is done or the user
  manually detaches it. Pausing alone does not unlock it. Detachment stops all
  further automatic source updates, LLM calls, reviews and repairs. User-created
  trajectories are detached by default.
- Manual review/repair is permitted only on detached trajectories. It is an
  explicitly requested manual operation, not reattachment or automatic work.
  Lock while the operation runs, then leave detached; do not start automatic
  follow-on reviews/repairs from a manual action.
- Automatically detach a trajectory when its automatic pipeline completes.
  Abandoned trajectories after failed repair are also detached at archived
  Unfinished. Completion is terminal, unlike a pause with pending pipeline work.
- Detachment is permanent: no reattachment. Explicit manual edit/review/repair
  remains available; new automated generation creates a new trajectory identity.
  Do not resurrect an old pipeline through restart, resume, endpoint reconnect
  or undo.
- Store user Undo/Redo in browser-local cache, with maximum retained action count
  configurable in Preferences. It is client editing history, separate from
  project source/review provenance and automatic job checkpoints.
- Configure endpoint concurrent-request limits independently from the active-
  trajectory cap. Review and repair share a strict one-operation-per-trajectory
  limit: no overlapping Fast/Critical reviews or repair sessions on one source.
- Defer Remix spending-cap enforcement to a possible future feature. Run with
  user-configured provider/API-key limits; if the API stops responding, pause the
  affected generation attempt and preserve work. This is not a trajectory-quality
  failure, repair verdict or permission to switch models/backends silently.
- API failure pauses new dispatches to the failed endpoint as well as the
  affected attempt. Other endpoints may continue within limits. Preserve queued
  work and do not fan the same unavailable endpoint out across all trajectories.
- Before marking an endpoint dead, retry API failures a configurable number of
  times. Distinguish transient retrying from exhausted/dead endpoint state and
  keep this budget separate from structural event-generation retries.
- After retry exhaustion, dead endpoints require explicit manual resume. No
  further background probes/retries or silent revival on server restart. Keep
  affected attempts/checkpoints paused and unrelated healthy endpoints running.
- Export opens a configurable menu: minimum stage Ready/Validated/Candidate/
  Pending (Ready default), metadata trimming, raw versus compiled output, and
  shard size measured in trajectories per file. Raw files are .jsonl; compiled
  files are .ktremix. Negative/Unfinished stages are not threshold choices.
- Organize prompts and ready requests to improve provider prompt-cache reuse:
  stable shared prefixes before dynamic context, consistent role/schema rendering
  and grouping compatible requests. Context isolation and causality take priority.
- Masking and loss settings apply to an entire event, not internal text spans.
  Tool-result events are masked by default. For a masked word/passage, chain
  separate unmasked/masked/unmasked events; do not add per-segment mask editing.
- Tokenize each event independently. Tokens must never cross event boundaries;
  the user confirms this avoids ambiguous loss assignment and fits Remix owning
  tokenization. Runtime window-content replacement remains required, including
  authored tool-driven TODO updates.
- On replacement, remove old content and insert the first replacement token in
  the same compiled tick, then stream remaining tokens if applicable. The window
  is never empty, remains Active and keeps its logical identity/physical slot.
- Remix handles headers automatically for agent-created and external-created
  windows. In the agent-created case, the agent is expected to populate its
  header; external-created windows get an automatic header. Headers inherit the
  initializing source and its event mask/loss. External headers are context and
  masked by default, not automatic agent prediction targets. Available is BOS-only.

User reports graph authoring improved preparation, compilation and review in
preliminary tests. The particular tested graph schema was not supplied here.

## Repository findings

| Area | Current implementation | Consequence |
| --- | --- | --- |
| Compiler | `compiler.py`: `Compiler.compile` expands ticks, resolves anchors, simulates tools and creates token rows. Shared instances serialize through an RLock and retain one trajectory prefix cache. | Remove it from all authoring paths; isolate export compilation contexts. |
| Construction | `llm.py`: `SampleBuilder` advances `next_tick`; `content_first.py` and `scenario_runner.py` compile staged/final drafts. | Replace the builder with graph operations and source validation. |
| Flow | `scenarios.py`: version-1 YAML, `needs`, explicit inputs, role profiles and tag pools; `ScenarioRunner.start` samples tags. | Keep useful orchestration semantics; introduce a structured proposal contract. Flow dependencies and trajectory dependencies are different graphs. |
| Parallelism | `ScenarioRunner` owns one builder, snapshot, status and draft. `execute` runs model-only siblings concurrently; `run` processes sample attempts sequentially. | A larger `max_parallel` does not provide concurrent trajectories. |
| Requests | `model_session.py` already supports OpenAI-compatible streaming, structured output, usage and optional inference-tokenizer budgeting. | Reuse tested provider behavior behind an independent request service. |
| Persistence | `storage.py`: SQLite/WAL, lazy rows, stable UUIDs, metadata/search projections, project revision checks. | Retain SQLite; introduce per-trajectory conflict checks and atomic job publication. |
| History | `changes.py`: bounded process-local undo/redo, project-wide tip; outside changes clear history. | Background generation must not populate/reset unrelated editing history. |
| Recovery | `generation_jobs` holds JSON snapshots; recovery/resume services archive failures and check source ownership. | Preserve recovery guarantees while normalizing job/attempt ownership. |
| Review | `validation.py`, `review.py`, authoring/MCP handlers rely on compiled views and tick/index evidence paths. | Review graph source and recorded observations; address evidence by stable block IDs. |
| Approval | `dataset.py` preserves validation level on edits while clearing assessments; Ready=3 gates training export. | Revision-bound approval would be a policy change and needs discussion. |
| Metrics | Existing source coverage/observed annotations and derived compiler measurements; advisory launch rules, no adaptive controller. | Retain source coverage; compiled measurements become export-derived, explicitly versioned data. |
| UI | `static/app.js`: 2-second `/api/state` polling, project-revision refresh, one `draft_id`, tick replay/editor. | Replace single-job following and tick editing with job lists, block views and incremental updates. |
| Training | `training.py` and `docs/EXPORT.md`: `kidra-token-chunk/1`, external-first phases, durable-only KV, NOOP targets. | Preserve this boundary and test it independently from source representation. |

The sampler Python/JSON artifacts were supplied as conversation attachments on
November 12; they are inspected design inputs, not installed/committed code.
Existing `build-graph-analysis.json` examples use tick replay and are not the new
dependency-graph source format. Initial repository findings came from inspection;
the later sampler verification below is a smoke check, not a trajectory benchmark.

## Proposed boundaries

Authoring path:

`proposal -> Flow requests -> graph edits -> source validation/tool observations -> review`

Export path:

`pinned source revisions + export profile -> graph lowering -> token compiler -> export validation -> published artifact`

Opt-in visual preview path (subsequently requested by user):

`source snapshot + client-selected project tokenizer profile -> shared tokenization/scheduler -> Tick history rendering`

This preview writes no source, metadata/metrics, training dataset or export
artifact. Do not implement a second approximate scheduler: reuse the scheduling
core so the view reflects that profile's compiled timing. Graph mode requires no
tokenizer. The preview is an explicit visualization exception to the earlier
absolute ban on tokenizer/scheduler work outside export, not a new hidden
compilation path for generation/review/metrics.

Source validation checks schema, references, cycles, window lifecycle, writer
ordering, tool relationships and supervision structure without tokenizing the
trajectory. It cannot certify tokenizer alignment or final context/slot budgets.
An editable or reviewed trajectory is not thereby guaranteed exportable under
every profile. Export failures report source block IDs and leave source intact.

Avoid introducing a general workflow platform or replacing FastAPI/SQLite as
part of this change. Proposed modules can begin as a few cohesive files:

- `graph.py`: typed source model, references, graph validation and edits.
- `graph_views.py`: block timeline, window history and bounded review evidence.
- `graph_tools.py`: request fragments, authored observations, project tool schemas
  and advisory call validation; no executable simulation in the new workflow.
- `graph_lowering.py`: shared scheduling core for export and opt-in Tick history;
  training-array/artifact emission remains export-only.
- `sampling.py`: versioned sampler configuration/proposal adapter.
- `jobs.py`: durable coordinator and job transitions; isolated Flow execution contexts.
- Extract provider/request behavior from `ModelSession` rather than duplicating it.

Names and file layout are provisional; behavior and boundaries take priority.

## Source graph contract — proposed

Confirmed schema rule, clarified by the user's subsequent numbered answers:
metadata is entirely optional. Primary trajectory fields/sections contain data
needed to interpret/compile the trajectory: graph/windows/dependencies,
supervision, lifecycle semantics and necessary format/identity fields.
Remix workflow requirements do NOT make a field intrinsic trajectory data.
Stage, split, review signoffs (including evidence/model/revision/staleness), and
family/group IDs remain metadata because compilation does not depend on them.
Removing metadata preserves trajectory validity and compilation, but need not
preserve optional Remix workflow history. Another consumer may validate samples
differently or not at all. Optional metrics also remain metadata. This corrects
the assistant's earlier overbroad interpretation of the primary-schema rule.
Exact primary field mapping/import behavior remains to be designed; no source
migration is performed during this planning discussion.

Use the new explicitly versioned source format; legacy formats are unsupported.
Retain immutable trajectory UUID and editable human label. Each block has a
stable ID, operation, logical window, explicit predecessor/milestone references,
content/arguments, supervision annotations and provenance as appropriate.

Candidate block operations: insert external context; generate text; invoke tool;
deliver observation; replace window content; author-only fixture setup. Exact
operation names remain open. Agent window activation follows Progressive Window
Assignment rather than arbitrary explicit opening. EOS remains the closure
mechanism; do not introduce an independent authoring close operation by default.
Keep existing tool/message/context spellings out of author-authored timing logic.

Dependencies express execution constraints, not permission to read arbitrary
authoring information. Preserve explicit Flow inputs and model-context selection.
Distinguish the authoring model's privileged world/fixture context from the
observations available to Kidra in the resulting training trajectory.

Confirmed event deletion: leave other events intact with broken references;
provide a one-click action to remove each broken reference. Do not silently
delete dependent events or reconnect dependencies to predecessors. Unresolved
structural reference errors block compilation under the normal diagnostic rules.

Confirmed event scheduling: dependencies wait for full predecessor completion.
An event executes immediately when all dependencies finish; roots execute at
trajectory start. Partial observations use separate events, not positions within
a larger block. Independent events targeting different windows may overlap.
The later external-chain boundary checkbox is the explicit exception to default
immediate chaining; it does not add arbitrary tick timestamps or durations.

Each event targets exactly one window. For any two events targeting the same
window, one must be an ancestor of the other; otherwise flag a potential
collision. A shared ancestor, shared descendant, or list order does not establish
ordering between sibling branches. Validate reachability across the entire DAG,
not only direct dependency lists. Reject cycles before checking ancestry. A
backward ancestry walk can establish comparability without token compilation.
Do not resolve unordered same-window events by arbitrarily serializing them.

Confirmed lifecycle: Progressive Window Assignment, mandated by the user's test
findings. Active means containing text; Available means empty and writable.
Exactly one Available window exists at all times. An agent write turns that
window Active and immediately creates a replacement Available window. External
tools may create populated Active windows directly without consuming Available.
EOS closes windows. The dashboard omits the empty Available window; this does
not authorize omitting it from model execution or training.

Every authored window has a unique stable ID. It is opened on the first frame
any text is written to it. Empty Available windows are not authored source
entities: the compiler inserts them automatically. First agent writes bind the
stable authored window to the Available window as it activates.

The user identifies simultaneous first writes/new-window creation as a collision
as well. Keep two collision cases distinct: unordered writers to the same stable
window violate graph ancestry, whereas coincident creation of distinct windows
requires compiler allocation handling for agent-created windows only. If two
agent-created windows would open at the same time, compilation uses deterministic
ordering and pushes one back one tick. External-created windows are exempt and
may open together or alongside an agent-created window. Do not add a hidden
dependency to the authored graph. Preserve dependency constraints by propagating
delayed event completion to dependent starts. User approved deterministic ordering
and this propagation; window-ID order is the proposed tie-break. Recheck for
further allocation collisions after delays so cascades remain deterministic.

Confirmed capacity and identity: closing a logical window permanently ends that
ID's lifetime; it cannot be reopened/reassigned in authored source. Compilation
randomizes physical slot allocation and can reuse vacant slots after closure.
The configured count is peak simultaneously open Active windows, including
external-created Active windows, excluding the Available window. Total distinct
logical windows can exceed this peak. Preserve EOS phase/lifetime semantics when
determining that a physical slot is vacant; clear prior occupant state before
reuse and keep logical IDs distinct from physical slot numbers.

Implementation proposal: use a seeded randomized allocator, persist the export
seed/policy, and allocate among vacant slots so exports can be reproduced without
requiring fixed logical-to-physical identities. This proposal does not constrain
training-time remix policy, which remains a separate export decision.

Confirmed Available representation: BOS conditioning only, no header or authored
text. It predicts NOOP on every tick it remains Available, with the usual NOOP
loss. BOS does not activate a window. The first agent text activates that same
window, and the replacement Available window receives its own BOS. Keep these
implicit targets in compiled exports and exact token/loss metrics, without
materializing them as source graph events or dashboard windows.

Confirmed Active header behavior: Remix automatically handles headers for both
window origins. Agent-created windows are expected to have agent-populated
headers; external-created windows receive automatically generated headers.
Latest clarification supersedes the earlier overbroad interpretation that every
header is agent-written: a header belongs to whatever source initializes the
window and inherits that source/event's masking and loss. Agent-created headers
are agent-authored targets unless the opening event is masked; external-created
headers are external context and masked by default. User rationale: supervising
external document headers as model targets could teach invented document windows.

No independent header loss settings. Proposed lowering treats the header as a
prefix of the first event, preserving its source and supervision rather than
unconditionally injecting zero-loss or supervised header rows. User confirmed
agent-created headers, including CTX framing, are emitted one token per model
tick; external-created headers are populated atomically before first content,
even when that content is streamed. External-created windows still
bypass Available and are exempt from agent-opening staggering. The reserve
Available window stays BOS-only. Header insertion is automatic authoring support,
not a change of provenance from external input to model output.

Confirmed framing depends on source/event, not a blanket per-token rule: chat
MSG framing is inserted all at once via the chat handler; agent tool calls are
emitted one token per tick; tool results are inserted atomically per instant
partial event, retaining the previously supported per-event streamed alternative.
All framing inherits the corresponding event's masking/loss.

Confirmed dependency phase rules: external input completing in tick N's input
phase can enable agent generation in that SAME tick. Completion of an agent
event in tick N's generation phase enables dependent events no earlier than N+1.
Completion includes its generated framing/endings under the existing event rules.

Confirmed EOS lifecycle: close immediately after emitting EOS in tick N; the
window is absent before tick N+1. EOS is an output target, never a visible input
or retained KV key. Preserve its loss as specified by the closing event.
User clarified: during tick N's forward pass all other windows can see the full
pre-existing contents of the closing window, excluding the current generated
token EOS. Close immediately after that emission; no window context persists
into the next tick. The earlier ambiguity is resolved.

Confirmed external-chain choice: offer a checkbox on the receiving external
event so the author can choose immediate same-input-phase chaining or a separate
compiled update on the next tick. Default is instant/immediate. Proposed positive
label: Instant chaining (checked by default). The option controls execution-step
separation, not the independent Instant/Streamed content-delivery setting.
Dependency-ordered instant appends may share an input phase, including ordered
writes into the same window; unordered same-window branches remain errors.
The unchecked/separated choice puts the successor in the next tick's input phase.
This does not override agent-completion→next-tick constraints or root start rules.
Store this compilation-affecting choice in primary event fields, not metadata.

Arbitrary authoring delays/wait durations remain excluded. Dependencies control
eligibility, with the explicit external input-step separation option above.
Progressive assignment
supersedes any proposed arbitrary opening/slot policy where they conflict.

Confirmed reasoning effort: the reasoning-window header carries an effort label,
selected by a dropdown at window opening. Defaults for the project's list are
Low, Medium and High; the list/stages are configurable in Project Settings.
Persist the choice on logical window metadata and render it through the existing
automatic header path, inheriting opening-source supervision. Do not require a
new explicit open event or show the reserve Available window to select effort.

Implementation proposal: expose the project levels to generation so generated
reasoning windows choose a valid label too. Keep labels separate from inference
provider reasoning-effort settings; this is training-trajectory header content,
not a request to change OpenRouter/model API options. A client default selection
and the handling of removed/renamed project levels can be specified with the
concrete settings/schema design.

Confirmed sleep rule (refined): if all Active windows emit NOOP on one tick,
model execution suspends. Read-only windows, including documents and system
prompt, count as NOOP without asking the model to write into them. Sleep continues
until something changes in visible state: new text, newly created windows,
replacement content, etc. From the model's point of view only a single tick
passes; no repeatedly advanced model clock or repeated idle targets during sleep.
The reserve Available window retains its established BOS/idle-NOOP behavior; an
agent write into it activates a new window rather than being ignored by sleep
detection. Preserve the entering all-NOOP decision tick; exact wake-delivery and
phase mapping belongs to export design.
This and Progressive Window Assignment are explicit refinements to the earlier
goal of retaining the compiled execution contract; do not preserve old idle-row
expansion where it would contradict sleep.

Unrelated blocks are not automatically invisible to each other: compiled Kidra
visibility remains governed by shared open-window context and the existing
external-first rule. Lowering must preserve intended observation ordering,
including negative constraints where necessary. Arbitrary topological sorting is
not an adequate scheduling specification.

Confirmed supervision: masking and loss apply uniformly to the entire event.
Tool-result events are masked by default; generated text is supervised by default,
with user-selectable event masking/loss controls. To mask selected words, split
the text into chained events and mask the middle event. No internal text spans or
segmented-loss schema in new graph events. Do not add legacy segmented imports.

Confirmed tokenization policy: retain the current compiler's per-event boundary
(`Compiler._encode` encodes one event). Tokenize each graph event independently
on export, then apply its uniform mask/weight. Do not merge adjacent events before
tokenization across masking boundaries or insert visible separator text. This
avoids a token straddling two event-level loss policies without author-time
tokenization. Exact encoding may differ from encoding concatenated window text;
preserve event boundaries as part of the export contract.

Confirmed content operation: events can replace existing window text, not only
append. User example: a tool call updates the TODO list. This is an operation in
the authored trajectory, separate from editing historical source. With no
sandbox, the replacement content is explicitly authored.

Current compiler reference: on first delivery of a replace-content append event,
mark previous durable non-open/non-header rows removed at that input frontier,
preserve BOS/headers, and insert new content. Earlier history remains inspectable
and available to earlier decisions, not retroactively erased.

Confirmed replacement boundary: delete immediately and deliver the first new
token in the same compiled tick; stream subsequent tokens normally. Never expose
an empty window between clearing and insertion. Replacement does not close or
reopen the logical window, release its slot, consume Available or change its
Active classification. Empty-payload replacement cannot satisfy this rule and
needs an explicit diagnostic rather than silently making an empty Active window.
Version the replacement randomized, lifetime-aware slot policy explicitly; the
old total-distinct-window budget and unique-slot-per-logical-window assumptions
must change. Preserve stable graph traversal and persist allocation randomness
for repeatable exports. Exact peak Active counts belong to a resolved export
schedule; do not present a graph-width approximation as an exact token-schedule
measurement during authoring.

## Synthetic tools — proposed direction and unresolved semantics

Current tools are tick callbacks that read windows, mutate shared sandbox state,
stream results and receive cancellation callbacks. Their output can depend on
the tokenized request duration and tick clock. Moving only the authoring schema
does not remove these dependencies.

Confirmed decision: abandon executable sandbox simulation and author tool
observations manually. Tools become premade and user-created JSON schemas in
Project Settings, with form-based editing rather than requiring raw schema JSON.
Reason: sandbox complexity increased authoring effort without sufficient benefit.
Code removal/migration happens during implementation after design; do not delete
old modules as part of the current discussion.

Confirmed result delivery: represent results as a series of events. Each is
instant by default and depends on the previous result event by default. Optional
dependencies may reference external events. Each result event independently
selects instant or streamed delivery; do not impose one delivery mode on the
entire call. A downstream dependency waits for full completion of a streamed
event, as for any other event. Default predecessor links are authoring convenience;
custom dependencies must still obey same-window ancestry/collision constraints.

Confirmed delivery timing: streamed chunks have configurable rates. There are
no authored tick offsets, delay fields or wait nodes: an event starts when its
dependencies complete, subject to export's required allocation handling. User
rationale: in their test campaign timing/waiting was consistently near-perfect,
and varying waits from 1 to 10 to 100 ticks mattered little. This is a reported
finding about that campaign, not an independently reverified universal claim.
Do not add a delay abstraction to reproduce timing that did not improve learning.

The call/request itself must also support
multiple partial events, so the agent can pause construction, obtain more
information, and then continue the same request. User example: start composing
a log-file write, query datetime, then finish composing the write.

Proposed model: stable call ID linking request fragments and result fragments;
stable target window IDs; dependency chains order fragments. A fragmented
request is allowed to be syntactically incomplete between fragments. Validate
the completed request/schema at the submission boundary, rather than reject a
prefix as malformed. Schema mismatches are warnings only, including on submitted
calls; schema nonconformance alone does not block saving/export. Request
construction and simulated execution in the authored trace are separate concepts.

Confirmed submission/cancellation: tools start only after submission of the full
request. They do not consume or respond to a request while it is being authored.
EOS before submission closes the window and abandons the unfinished call. This
is a valid pre-submission cancellation, not malformed submitted JSON; do not
require a complete request/schema for an abandoned prefix. Such a call never
starts or produces result fragments. Source checks should reject result fragments
linked to an unsubmitted/cancelled call. The user subsequently simplified the
representation: `end-tool` and `close-window` are explicit event checkboxes/flags,
rather than discovering submission/closure by scanning content. The protocol
still renders the tool ending and EOS, but graph semantics come from flags. No
separate submit/close operation is required; the last text fragment carries the
appropriate flag. This supersedes the earlier delimiter-scanning proposal.

EOS is always the final token in a window by design. Closing after submission
therefore cancels remaining output to that window while preserving already
delivered observations. Do not emit further result tokens or reopen that logical
ID. Exact treatment of source events that attempt to deliver after closure
(diagnostic versus explicit cancelled-event records) remains a validation/UX
detail; it must never result in emitted tokens after EOS.

Latest confirmed simplification supersedes ALL earlier manual-insertion and
canonical-text-placeholder proposals. Authors/LLMs supply content and structural
flags, never protocol-token spellings/placeholders. Remove the insertion-mode
preference entirely. Derive special tokens from:

- BOS/NOOP: compiler window initialization and scheduling.
- EOS: Close window.
- CTX framing: automatic window-header generation.
- MSG framing: a dedicated chat-interface/message handler.
- Tool-call events: independent Insert tool begin / Insert tool end checkboxes.
- Tool-response events: independent Insert tool response begin / Insert tool
  response end checkboxes.

The call's tool-end flag remains submission authority. Independent begin/end
flags support a first fragment with begin only, middle fragments with neither,
and a final fragment with end only; do not wrap every partial event as a complete
request/response. Saving an event does not implicitly submit it or close a window.
Keep response content free-form and per-event instant/streamed controls unchanged.
Flag names in the source schema remain provisional; checkbox meanings are settled.

Render generated special tokens as distinct bordered blocks with uppercase
labels (e.g. EOS). The dashboard derives these decorations directly from source
structure, without tokenization/compilation. Store flags and ordinary content,
not rendered HTML, text token placeholders or the chosen tokenizer's spelling.
Map logical structural control kinds to the selected tokenizer only on export.

Errors block compilation; warnings do not. Structural protocol/flag errors are
errors. The earlier example of a manually missing EOS is obsolete because EOS
is now derived from Close window; do not require duplicate literal tokens in
content or report their absence as an error. Draft saving remains distinct from
export-only compilation. Tool input-schema mismatches remain advisory warnings.

Confirmed: combined end-tool + close-window flags are allowed. Emit the tool
ending before EOS, submit the completed request and then close the window. No
subsequent content may be appended to that closed window; preserve the existing
result-cancellation semantics. New-format raw imports use structural flags rather
than scanning/normalizing authored special-token placeholders.

Example intended dependency chain: log-request prefix -> datetime request ->
datetime result -> log-request suffix/submission -> log-result fragment 1 ->
log-result fragment 2. Datetime uses a separate window. The original log window
remains active during the pause, and compiled idle targets follow existing rules.

Under the confirmed manual-output approach, generation/human authors supply the
observations; authoring/review/export perform no synthetic tool execution. Keep
tool identity, project schema version/reference, request/result linkage, provenance
and explicit outcomes. Structural checks validate graph references/order and
protocol lifecycle; tool-argument/schema checks report advisory warnings.
Semantic review checks consistency with scenario facts and
earlier observations. Do not claim authored outputs are execution-verified.

Confirmed: do nothing to other events when an event is edited. Do not mark
linked tool responses or downstream reasoning/chat/document events stale or
contaminated, add request-change reminders, or regenerate their content. Semantic
consistency belongs to the author and reviewers. User rejected the special
response reminder because the same theoretical issue applies to every event
and propagating contamination would be unreasonable. This does not remove
structural validation (missing references/cycles/lifecycle) or the separately
confirmed policy that source edits mark saved review signoffs stale.
Timers, alarms, watchers and subagent progress would
be authored observations with graph dependencies under this approach. Explicit
delay/wait scheduling is out of scope by decision; timestamps may remain content,
but they do not introduce a hidden authoring clock.

Earlier executable-state, sandbox-hash, reevaluation and new Python plugin adapter
proposals are superseded for the graph workflow. User subsequently excluded all
legacy format compatibility, including old tool/plugin and checkpoint conversion.

### Project tool catalog and event editors

Confirmed UI:

- Project Settings contains starter tool JSON schemas and custom tool creation.
  These definitions belong to the project, not client Preferences.
- Schema editing uses form fields rather than requiring raw schema JSON editing.
- Tool events offer Form and Raw JSON modes. Form creates argument controls from
  the selected schema; Raw JSON allows direct text, including partial requests.
- Tool-call schema nonconformance produces warnings, not authoring blockers.

Proposed catalog entries: stable ID, name, description, version and argument
schema. The schema editor provides field names, types, descriptions, required
controls, enums and nested object/array editing. JSON Schema remains the stored
representation. Confirmed scope: schemas cover inputs only. Outputs are always
free-form, including arbitrary text or JSON as authored; no optional output
schemas or output-schema warnings. Graph lifecycle/dependency rules still apply.
User rationale: maximize flexibility for the author.

Form serializes structured inputs; Raw JSON preserves exact fragment text. Never
silently normalize/repair partial raw text. If a fragment cannot be represented
as a form, keep Raw mode and explain why switching is unavailable; do not discard
content. Form usage does not itself check end-tool or close-window.

Assess schemas against assembled requests, not incomplete individual fragments.
Unfinished/abandoned prefixes are not submitted schema failures. On submission,
show field/path-specific warnings without imposing admission gates. Schema edits
must not rewrite existing call text.

Confirmed schema-edit policy: replace/update the project definition and recheck
existing calls against the current definition, not a pinned old validation schema.
Only recheck trajectories using the changed tool and emit advisory warnings for
calls that no longer match project specification. Do not rewrite trajectories,
their authored observations or their historical schema text if present. Historical
provenance may remain available without controlling current compatibility checks.

Implementation: maintain a tool-to-trajectory reverse index transactionally on
source edits/imports/removals. The checkout's `trajectory_search_tools` association
is a starting point, but add a tool-first index (existing primary key begins with
trajectory UUID) and stable project tool identities. Query affected UUIDs directly;
do not scan/decode all 100k sources to discover usage. Read only affected calls
and update separate diagnostics keyed by tool version and source revision. Before
publishing, check both are still current so rapid schema/source edits cannot
publish stale warnings. This process performs no token compilation and no source
rewrites. Replace the current compile-error-on-schema-change policy.

## Sampler and Flow — proposed

### Supplied sampler contract and inspection

Received attachments: `kidra_sampler.py`, `kidra_sampling_matrix.v1.json` and
`kidra_example_definition.json`. Sampler/matrix schema version is `1.0.0`; matrix
ID is `kidra-practical-sft-v1`. Exact source identities:

| Artifact | SHA-256 |
| --- | --- |
| sampler Python bytes | e00a66f5be1131f83f390ff8784edc6d2207202262cea5a1a51b833b582b175a |
| matrix file bytes | 59e30228558b9db8f19e73b782be6070589a1dc6a241555a400286fcdc1bb853 |
| canonical matrix JSON | 5e41e190ff9349f57411461ba5626126ff9466a9b27da3c4fea8449c5ed28ff7 |
| supplied example bytes | 952a3d5c9468df8225e54a689305e576c0099f8388c67d29543dc32629d120c7 |

Read-only verification: inspected the complete Python source, loaded/validated
the supplied matrix, reproduced the example exactly from seed 137 and its saved
overrides, and generated 200 seeded proposals without exceptions. No repository
implementation, paid generation, sampler modifications or distribution study.

Public API: `sample_one(matrix, seed=None, world_group_id=None, overrides=None)`
returns exactly one proposed definition; it does not generate trajectory text.
Supported overrides: `focus.family`, `lifecycle`, `complexity`, `domain`,
`user_competence`, `user_patience`, `user_involvement`, `user_tone`.
Per-run override support is user-confirmed. Start with the supplied API's supported
fields; extend deliberately for new interaction dimensions as their schema is
finalized. Default is unpinned/Any. Persist requested overrides, allocated seed
and resulting proposal before generation; reuse on retry/resume. Incompatible
pins produce a clear configuration diagnostic rather than relaxing constraints
or silently changing requested choices. Project matrix remains unchanged.
Matrix-driven sampling uses 6 families, 43 primary behaviors, 22 decision axes,
24 categorical dimensions, compatibility constraints and joint marginal
balancing. Do not reproduce its constraint logic in Flow.

Output groups:

| Field | Adapter responsibility |
| --- | --- |
| definition_id/schema_version/status | Preserve original values as proposal data; status starts proposed. |
| sampling_provenance | Save seed, canonical matrix hash, sampler version, requested overrides, world group and unresolved split. |
| learning_objective | Supply behavioral objective, contrasting decision and condition making it valid to generation/review. |
| scenario | Supply concrete world constraints, resources, user characteristics, lifecycle, window/episode targets and additional event tags. |
| generation_directive | Preserve full requirements; translate representation to graph authoring without dropping learning requirements. |
| metrics.sampling/multi_labels/numeric_targets | Store intended coverage separately from observed behavior and exact export exposure. |
| metrics.actual | Initially empty; do not fill token counts before export or fabricate observations from requested labels. |

Important integration findings:

- `definition_id` hashes matrix hash + seed + world group, but excludes overrides
  and sampler implementation version. Verified two different domain overrides
  with the same seed produce different proposals with the same definition_id.
  Use a new storage UUID and full immutable payload digest, not definition_id as
  a uniqueness/deduplication key. Retain definition_id for provenance.
- Default world group is `provisional-world-{seed}` and split is null. Neither
  establishes independent scenario-family identity or a safe split policy; D06
  must assign actual families/groups and partitions explicitly.
- `metrics_report` accepts a stream of definitions; accepted-only means literal
  `status=accepted`. Produce a reporting projection from the registry's acceptance
  policy instead of mutating the saved original proposal or conflating finished
  generation with training acceptance.
- Horizon targets range from 3 to 70 coordination events. Current Flow repeat
  schema has a 20-iteration ceiling and bundled flows hardcode smaller loops.
  Redesign bounded episode continuation; do not equate one coordinator decision,
  one model request and one graph event. A decision may emit several fragments.
- `target_active_window_capacity` is a requested numeric target. Measure actual
  peak Active windows separately at export; exclude Available per confirmed rule.
- Sampler is not aware of project tool catalog/capabilities. Flow must reconcile
  scenario resources with available schema definitions without silently relaxing
  a learning objective or presenting a capability-conflicting proposal as valid.
- Existing seed draws are isolated `random.Random`; preserve deterministic inputs
  and allocate/persist a proposal before sending model requests so retries/resume
  do not silently resample. Different attempts remain distinct artifacts.

### Proposed integration shape

Confirmed numeric-target enforcement: advisory only. The user considers exact
matching of sampled back-and-forth/event counts unreasonable; issue warnings if
the completed trajectory drifts too far, rather than automatically rejecting it
or repairing it solely to reach an exact number. Keep project hard capacity
limits separate from sampled targets. Confirmed tolerance configuration: per
metric in Project Settings, with a percentage band plus minimum absolute drift.
User rejected a universal numeric default: tolerances depend on the targeted
metric. Max-window targets may allow more drift than tool-usage targets. Define
defaults per metric, still editable via its shared settings/definition; initial
specific values remain implementation design work, not a confirmed 20%/1 rule.

Proposed comparison rule: for target T and measured A, warn only when
`abs(A-T) > max(absolute_tolerance, abs(T)*relative_tolerance)`. For T=0, the
absolute tolerance governs; missing actuals remain unknown. Both upper/lower
drift use the same band initially unless a metric has an explicit one-sided
meaning. Include measured value, target and thresholds in the warning. These
are diagnostics only and do not mutate source, alter acceptance or force retries.

Implementation proposal: compare requested targets with actual logical decision,
task and dependency measurements, not raw fragment count (splitting text for
masking must not inflate behavioral coverage). Record target, observed value,
delta and tolerance for each warning; missing measurements remain unknown.
Exact peak Active-window/token measurements are export-derived and cannot trigger
an authoring-time compile. Source-level metrics can be checked without tokenizing.
Do not equate requested labels with observed behavior or force auto-regeneration.

### Sampler Matrix tab — readability-focused UI design

Confirmed location: Generation Settings > Sampler Matrix (project configuration,
not client Preferences). Existing settings has Endpoints, Generation Defaults and
Custom Guidelines tabs. Add a dedicated tab, rather than burying the matrix in
Defaults or exposing a giant JSON textarea. The user explicitly wants readability
because the raw matrix is currently difficult to understand. A separate PR for
usability refinement is appropriate; no UI implementation is authorized yet.

Proposed information architecture:

1. Overview: explain the pipeline in plain language: choose learning objective,
   choose compatible scenario conditions, then generate the trajectory. Show
   current matrix version, a few key distributions and actionable health issues.
   Explain that these are proposal intentions, not observed dataset coverage.
2. Learning objectives: browse 6 competency families and their 43 behaviors; choose
   one item to edit its objective, frequency, compatible stages/complexity and
   decision contrasts. Do not display all behavior detail forms simultaneously.
3. Scenario conditions: group dimensions into episode/lifecycle, participants and
   user behavior, environment/resources/tools, coordination structure and
   observations/outcomes. Include the upcoming multi-user/idle settings in their
   natural groups rather than requiring a new generation flow for each.
4. Rules and compatibility: separate hard eligibility/forced implications from
   soft frequency biases. Display readable sentences and links to affected
   choices, not an undifferentiated list of JSON rules.
5. Preview and diagnostics: sample a reproducible proposal without an LLM call or
   token compilation. Show a short human-readable brief first, then optional
   detailed fields, seed, constraints and full JSON. Add trace/explanation data
   where needed to explain forced or ineligible choices honestly.

Use a roomy tab with left navigation/search, a central focused detail pane, and
an optional contextual explanation/example pane. Support full-size/expanded
settings so dense information is not forced into a narrow modal. Use breadcrumbs,
human-readable names and descriptions, progressively disclosed advanced options,
and stable navigation while validation/preview updates. Do not regenerate the
whole form on the existing two-second settings/status poll or lose edit focus.

Control semantics:

- Label objectives as what behavior the example should teach. Put the valid-world
  condition next to the target decision, so users understand why it is correct.
- For top-level competency/lifecycle/complexity distributions, show target shares
  rather than unexplained floating-point weights. Explore percentage/rebalancing
  versus relative-frequency controls across the three design branches rather
  than asking the user to choose these implementation details before trying them.
- For conditional choices, explain that a relative weight is not a global chance.
  Eligibility, forced choices and conditional biases can change realized shares.
  Never label every raw weight a percentage of the entire dataset.
- Example rule text: “When the entry state is a conflicted handoff, require
  conflicting information.” Distinguish this hard rule from “When optimizing
  resources, make recurring work 2.3 times as likely.”
- Show proposed settings changes and invalid combinations clearly. Keep incomplete
  editing drafts without silently weakening constraints. Apply only a validated
  matrix as the active generation configuration; preview errors explain what to
  fix and link to the affected controls.
- Keep JSON import/export and an advanced source view as escape hatches, with
  round-trip preservation of unknown extension fields. This is proposed UI scope,
  not permission to discard custom configuration the editor cannot render.

Suggested implementation boundary: a dedicated matrix-editor module, isolated
from the growing static/app.js, backed by the same canonical JSON matrix and
sampler API used by generation. Form changes produce the matrix draft; validation
and preview use the actual sampler rather than a second approximation of its
constraints. No paid generation, dataset rewriting or token compilation is needed
to edit/preview the matrix. Record changes as project configuration versions.

Dedicated usability PR acceptance tasks: a user can find a behavior, explain its
learning objective, change its frequency, understand a constraint, add a choice,
preview the resulting scenario, and recover from an invalid configuration without
reading raw JSON. Test keyboard navigation, focused edits, error placement,
readable dense lists and preservation of custom fields. The refinement PR should
include actual browser review, not only DOM assertions or screenshots of a form.
The user subsequently chose three design branches instead of finalizing one
initial UI and then doing a single refinement PR. Use the workflow below.

### Three-branch matrix-tab experiment — confirmed workflow

The user explicitly requests three branches, each proposing a different matrix
editor design. Initial designs are proposals for review; expect multiple rounds
of experimentation and modification before finalizing a PR. Select the strongest
design, then make remaining focused tweaks. Implementation details for each
alternative are delegated to the assistant. This is a plan for the implementation
phase, not an instruction to start coding during the design discussion.

Proposed branches and distinct approaches:

| Branch | Interaction design | Controls/implementation emphasis |
| --- | --- | --- |
| design/sampler-guided | Guided editor: overview, left category navigation, one focused detail form with contextual explanation. | Vanilla JS detail modules; top-level target percentages with proportional rebalancing; progressive disclosure for rules and advanced values. Optimize learning and first-time comprehension. |
| design/sampler-workbench | Comparison workbench: searchable/filterable tables, inline edits, visible relationships and a detail drawer. | Structured grid/list components with keyboard editing; raw relative weights paired with normalized/conditional interpretations; bulk edits where safe. Optimize comparison and repeated expert edits. |
| design/sampler-scenario | Scenario-first editor: human-readable sampled brief as the main view, with links from each scenario attribute to its controlling settings. | Preview cards/inline controls, frequency sliders with exact values available, linked rules/constraints and before/after examples. Optimize understanding changes through concrete generated proposals. |

All three remain dedicated Sampler Matrix tabs in Generation Settings; do not
turn alternatives into three separate products or ship a permanent design-mode
selector. Each needs a path to editing the complete supported matrix rather than
being a mockup of a few attractive fields.

Shared foundation: one canonical matrix draft format, project persistence/version
API, actual sampler preview/validation service, import/export and fixtures. Each
branch uses the same backend semantics, matrix snapshot and example seeds. Keep
this foundation separate from visual/interaction choices; do not independently
rewrite constraint sampling or add different generation behavior to each branch.
Use isolated worktrees during implementation so prototypes do not collide.

Comparison workflow:

1. Establish the shared functional foundation and representative matrix fixtures.
2. Implement three genuinely different end-to-end editor proposals on the named
   branches. Use separate draft PRs for review when implementation is authorized.
3. Provide runnable previews and the same task walkthrough on every branch:
   locate a behavior, explain a restriction, change frequency, add a choice,
   preview a seed, fix invalid settings and import/export without data loss.
4. Review readability first, then navigation/editing friction, task completion,
   accurate sampling explanations, error recovery, keyboard support and behavior
   with realistic dense configuration. Capture user feedback against a branch
   and concrete interaction rather than imposing an arbitrary scoring formula.
5. Iterate the proposals as needed; no fixed one-pass limit and no premature
   final PR. Apply shared correctness fixes to the foundation/other branches so
   a prototype is not preferred merely because another has a repairable bug.
6. Select one branch with the user, keep alternatives unmerged for reference,
   make the remaining tweaks and finalize the chosen PR. Do not merge all three
   competing UIs or delete alternatives during the discussion.

Distribution-control preferences and exact panel composition are deliberately
deferred to hands-on comparative review. Existing confirmed requirements remain
common to all branches: client/project separation, no data loss, honest weights
versus probabilities, deterministic source-only previews, and no LLM costs or
token compilation for matrix editing.

One adapter creates a durable proposal artifact, then invokes a selected Flow
with explicit `proposal` input and separate project/tool context. Save matrix
contents/hash, sampler implementation digest/version, seed/overrides and the exact
proposal before the first paid call. Flow receives proposal references rather
than a lossy tag conversion. Retain original proposal through repair/rejection;
append observations, review state and attempt provenance separately.

### Chunked generation and causal prompt construction

Confirmed generation granularity: chunks. This supersedes the proposed hybrid
of whole-graph generation for small episodes and chunks for large ones. The
dependency graph determines the text from accessible open windows available to
the LLM for each chunk; exclude parallel/future events and closed windows so
information the model has lost does not silently reappear in its reasoning.
Here "available windows" means causally accessible windows, not only the singular
empty Available reserve used by Progressive Window Assignment.

Confirmed request boundary: exactly one event per event-authoring LLM call.
Multiple events in a row are separate calls, even in the same window. User
rationale: multi-event output is mainly useful for dependency/parallel-aware edge
cases, which are extremely rare and not worth planning around when consecutive
calls are trivial. Do not add an opt-in multi-event mode now. Shared setup/review
requests are separate task types, not exceptions that can author multiple events.
The deterministic chatroom-entry helper still creates its explicit graph event
as previously agreed; this does not mean an LLM authored two events in one call.

Persist one generated event and its request result/checkpoint at each successful
step, then recompute the next request's causal context. Parallel independent
events remain isolated requests. This boundary simplifies attribution of masks,
scoped nudges, failures/retries and source references.

### Programmatic event envelopes and bounded failure handling

Confirmed event generation principle: the LLM produces the minimum necessary
content; Flow wraps it as an event. One LLM call corresponds to one event, not
necessarily a model-generated event JSON object. For agent reasoning blocks and
user messages, every event specification can be selected programmatically and
the LLM only generates text. User-message assembly inserts chatroom framing,
speaker identity and the correct settings. Avoid asking the model to copy IDs,
dependencies, masks or protocol markers the application already knows.

Programmatic envelope proposal: stable event/window/call identities, dependency
list, source/speaker role, delivery and rate, append/replace choice, whole-event
mask/weight, and known submission/closure settings. Which values are deterministic
depends on the Flow task; do not fabricate content decisions merely to avoid a
necessary model request. Apply project header/effort and insertion settings at
the proper authoring boundary without leaking private control metadata into the
training text.

Prompt output contracts should match task complexity: plain text for content-only
events; narrow structured semantic fields only where needed. Do not require a
large event schema for every response. Keep stable prompt prefix reuse across
matching tasks and event-local response artifacts for reproducible retries.

Confirmed failure policy: local structural/authoring failures can retry the failed
event with a concise diagnostic up to a configurable limit; then pause that
trajectory while other jobs continue. Preserve successful prior events and failed
proposals. Keep transport retries separate. Never retry/reject solely because
tool arguments have an advisory schema warning or numerical targets drift.
If the generated text is valid but Flow constructed invalid references/settings,
report a deterministic wrapper failure; repeated model calls cannot fix that bug.
Intentionally flawed exploratory content is not automatically a structural error.
Confirmed retry/stage ordering: exhaust the bounded local retry budget first.
An invalid event attempt alone does not demote the trajectory. If attempts are
exhausted without a valid event, immediately mark the trajectory Unsuccessful and
retain successful prior events, failed attempts and diagnostics. Other jobs
continue. Attempt Repair is a separate operation, not an unbounded extension of
the event retry loop. Deterministic wrapper failures and transport failures remain
classified separately as above.

Tool results require testing: their authored content and potential window effects
may be more involved. User explicitly requires a test-backed decision rather than
assuming a universal text-only template or delegating the whole event to an LLM.
Plan an implementation spike with simple returns, nested/partial JSON result text,
errors, multi-chunk output, free-form documents, TODO replacement, chat filtering/
rejection and cross-window effects. Compare programmatic-envelope text generation
with a minimal internal semantic response where necessary. Any internal authoring
contract must not become a schema restricting the simulated tool's free-form
output. Keep one event per call and graph/source validation throughout.

Measure wrapping correctness, correct causal dependencies/targets, content/trace
quality, retries and output/input-token overhead on the same representative cases.
Use deterministic fixtures/mocks for protocol checks; model-backed evaluation
belongs to the implementation/testing phase. No paid calls or live generation
tests have been run during this design discussion. Exact tool-result contract
remains an empirical decision gated by the spike.

Implementation requirements derived from that visibility rule:

- Build a fresh model-facing context at the requested causal frontier, using
  graph ancestry and current window state, not storage insertion order or a dump
  of the full generated trajectory. This view builder never token-compiles.
- Fold ordered ancestor events per window, apply replacements, and hide windows
  closed at that frontier. Old replaced text is not current context either.
- Do not retain a rolling provider chat history that includes closed windows,
  parallel generations, future plans, failed draft text or private evaluator
  feedback. Diagnostic/archive access is separate from the actor prompt.
- Concurrent independent chunks use isolated requests and causally appropriate
  frozen snapshots. Do not ask one autoregressive response to serialize multiple
  parallel outputs if its earlier output would leak into a sibling's generation.
- Save input source revision/frontier, selected visible event/window identities,
  prompt version and request provenance for debugging and safe resumption. Do
  not expose those archives as hidden memory to the model generating the trace.
- Access to old information is legitimate only through explicit visible retained
  memory or a newly observed retrieval result; no automatic resurrection of a
  closed window in an authoring summary.
- Context limits must not be handled by silently dropping arbitrary windows or
  supplying an oracle summary. Context-budget policy remains part of generation
  configuration and review.

Confirmed information boundary: Flow holds the full proposal and world/episode
state. It is the oracle selecting the next task, relevant context and high-level
nudge; LLMs receive only enough to produce their corresponding event(s). Hide as
much as possible from every request, including environment/result writers, rather
than automatically granting one LLM role the full proposal. Agent-writing input
still obeys causal open-window visibility. This refines the earlier proposed split
that would have handed the environment LLM all world facts by default.

Proposed scoped request roles within the same Flow: world/setup author, agent
event author, external observation author, and reviewer. Each receives explicit
selected inputs. A result writer may need a particular hidden current world fact
to produce an observation; that does not grant unrelated future facts or the
full expected trajectory. Authoring directives are separate from actual model
conditioning in the saved training example.

Nudges can specify the type of situation/behavior to exercise without dictating
the solution. Example: "reconsider the inherited plan using currently available
evidence" rather than revealing which future result disproves it or spelling out
the final choice. Store request/nudge provenance privately for review; never
inject hidden evaluation labels or private summaries into runtime windows. Exact
nudge templates will be part of the reviewed shared Flow implementation.

### Optional trajectory review stages and repair

Confirmed: semantic review is separate from event generation/source checking,
but may run automatically after a completed trajectory when enabled. The user
wants to restore tiered review given an actual generation/review budget; it had
previously been disabled for throughput. No live paid review is authorized or
run merely by recording this design.

The checkout already names all requested stages (`static/app.js`, stage labels):

| Value | Stage | Confirmed generated-trajectory transition |
| --- | --- | --- |
| -3 | Unfinished | Initial stage of every generated trajectory. |
| 0 | Pending | Generation completes. If Fast Review is disabled, remain here. |
| -1 | Unsuccessful | Structural failure after event retries are exhausted; or failed Fast Review. |
| 1 | Candidate | Fast Review succeeds. If Critical Review is disabled, remain here. |
| 2 | Validated | Candidate succeeds in Critical Review. |
| -2 | Rejected | Candidate fails Critical Review. |

Fast Review: Generation Settings toggle, mid-tier model, checklist of
qualifications, with notes explaining pass/fail. Critical Review: separate toggle,
more powerful model, triggered only by a successful Fast Review; it cannot bypass
Fast Review just because its own toggle is on. Both support manual triggering.
Endpoint roles/default enablement are defined below; review checklist contents/
configurability and detailed queue controls still need design.

Existing Ready (3) remains a human-set stage. Confirmed export policy now permits
a selectable minimum of Ready, Validated, Candidate or Pending, defaulting to
Ready. Do not hardcode Ready-only eligibility in CLI/API/UI training export.
Sampler reporting `accepted` still needs explicit mapping under D06.

Attempt Repair: eligible stages are Unsuccessful and Rejected. It is one combined
Repair & Validate operation, using the same model used by the respective review
tier: mid-tier for Unsuccessful, high-tier for Rejected. User rationale: a separate
same-model validation pass is redundant and likely to reproduce the same result.

Confirmed transitions:

| Starting stage | Combined outcome | New stage |
| --- | --- | --- |
| Unsuccessful | Repair & Validate succeeds | Candidate |
| Rejected | Repair & Validate succeeds | Validated |
| Either repair-eligible stage | Repair & Validate fails | Unfinished |

This supersedes the assistant proposal to reset to Pending and rerun both review
tiers. A successful repair includes a tier-appropriate validation verdict and
notes; source editing alone is not a success. Repaired Candidate can enter the
existing optional Critical Review path; do not rerun Fast solely because it was
repaired. Repaired Validated does not need a separate Critical pass.

Preserve findings, source revisions, patch/attempt records and the combined
verdict's model/checklist provenance. Stage failure is not deletion: leave the
trajectory and diagnostics accessible at Unfinished. Keep programmatic source
checks and safe revision-guarded publication, with no token compilation before
export. No unconditional guarantee that same-model outputs always agree is
claimed; the single-step behavior is the adopted workflow.

Confirmed failed-repair retention: keep attempted edits as the Unfinished working
source, preserving the earlier original/revisions and diagnostics. User rationale:
attempts are still likely better than the original, but returning to Unfinished
means significant modification is needed to become viable or the sample should
be explicitly deleted and recreated. It is primarily an archive step, not an
expectation of salvaging it through routine resume/retry loops.

Implementation proposal: keep visible Unfinished as the stage, but record why
(e.g. generation in progress/interrupted versus repair_failed_archived) in job/
operational metadata. Resume-Unfinished-generation discovery must exclude archived
failed repairs by default. Keep them inspectable/filterable for major manual
rework; an explicit restart/recreate action has separate provenance and preserves
the archive until the author chooses deletion. Do not automatically delete,
requeue or count archived failures as accepted/usable dataset coverage. This
distinction must survive restart, checkpoint loading and batch resume.

Confirmed invocation policy: all review and repair operations can be manually
triggered as well as run automatically. Automatic defaults on if its required
endpoint roles are available; the user explicitly considers manually adding
those endpoints implicit agreement to automatic use. Preserve explicit opt-out
settings. Do not ask for per-trajectory model-call approval once enabled.

Exactly three endpoint roles:

| Role | Work |
| --- | --- |
| Generator | Scenario/event content generation in the shared Flow. |
| Fast Review | Fast checklist review and combined Repair & Validate for Unsuccessful. |
| Critical Review | Critical review and combined Repair & Validate for Rejected. |

Users manually configure these endpoints. Repair has no separate endpoint or
model choice in the generation path. Missing review endpoints must not prevent
Generator-only creation; completed sources remain at the appropriate stage.
Critical Review still requires a successful Fast Review even when its endpoint
is configured. An unavailable/unconfigured endpoint is not permission to silently
substitute another role or report a quality failure.

Implementation proposal: distinguish endpoint configuration/readiness from an
explicit user opt-out, so reconnecting an endpoint does not undo a saved off
setting. Persist effective pipeline settings on jobs for reproducibility and
show the enabled path in Generation Settings. Provider transport interruption is
distinct from a completed repair-failure verdict; do not archive solely on a
transport error.

Confirmed repair budget: one attempt only. Repair continues until the model
returns a verdict or its context limit is hit. Context-limit exhaustion is a
failed Repair & Validate outcome, so retain attempted edits and move to archived
Unfinished. No configurable multi-attempt repair loop is needed. User rationale:
each attempt costs credits; difficult cases are better left for human rework while
generation resources go toward better new trajectories.

Confirmed session implementation: graph inspection and targeted edit tools in a
continuing model session, ending in one combined verdict. User prefers spot
repairs. Avoid whole-source regeneration when a local patch suffices; retain
unchanged events/window identities and provenance. Provide bounded event/window
history, causal-view inspection and exact revision-guarded edits with immediate
source diagnostics. No sandbox execution or token compilation in these tools.

One attempt must not silently restart, reset its context budget or compact away
history to evade the limit. Track consumed context/tokens and retain partial work
if the provider reports context exhaustion. Provider output caps/transport errors
need distinct diagnostics; do not claim a quality verdict that was never given.

Checklist adaptation work identified by repository inspection:

- `trajectory_review.py` already builds versioned checklists with evidence/rules,
  and `review_templates/review.yaml` groups layout, tools, reasoning and correctness.
  Reuse this structure where useful; tier membership is the next user decision.
- Replace tick/sandbox inspection assumptions with graph causal views and authored
  observations. Authoring review cannot claim exact token timing/context measured
  only by export.
- Adapt old automatic tool-managed notes rules to authored replacement events;
  handle the read-only chatroom/chat-call protocol and header provenance correctly.
- Respect whole-event masks and intentional flawed exploration context followed
  by supervised critique/correction. Review trajectory learning value, not a rule
  that every masked candidate must itself be correct.
- Keep advisory schema/drift/open-window diagnostics distinct from rejection
  criteria. Intentionally open windows and incomplete overall projects can be
  valid; a sample's episode objective determines completeness.
- Review/repaired-verdict evidence uses stable event/revision IDs and actual
  checklist criteria, with explicit not-applicable handling where appropriate.

Confirmed checklist relationship: shared core across Fast and Critical, with
additional Critical-only criteria. Fast filters obvious issues; Critical checks
the core as well as deeper reasoning/evidence and whole-trajectory consistency.
Use stable criterion IDs and versioned definitions. Corresponding Repair &
Validate checks the same tier's criteria on the repaired revision, with its
single combined verdict; do not add a new model pass to certify those criteria.

Proposed stock checklist (wording still a draft):

| Tier | Criterion ID | Qualification |
| --- | --- | --- |
| Core | objective_alignment | Scenario facts make the intended learning objective/decision meaningful; the episode demonstrates the specified behavior rather than merely mentioning it. Numeric targets remain advisory. |
| Core | causal_evidence | Decisions use only evidence accessible at their graph frontier; no parallel/future or silently resurrected closed/replaced content. |
| Core | actor_and_supervision | User/tool/context material and model actions have correct sources, chatroom delivery, header ownership and event masks. Evaluate intentional masked flawed examples in their teaching context. |
| Core | world_consistency | Authored observations, task/artifact identities, state changes and tool outcomes are mutually consistent; no invented execution or success claims. |
| Core | coherent_reasoning | Supervised reasoning/actions are intelligible and supported by the available facts; intentional flawed candidates remain distinguishable from endorsed behavior. |
| Core | truthful_episode_outcome | Completion, progress, blocking, sleeping and intentionally open work match the evidence and episode goal. No forced final user reply or fabricated project completion. |
| Critical | decision_quality | Decisions account for relevant alternatives, constraints, proportional delegation and resource tradeoffs, with substantive justification rather than ritualized planning. |
| Critical | long_horizon_continuity | Ownership, dependencies, handoffs and retained context remain coherent across user/autonomous phases, interruptions and multiple participants. |
| Critical | exploration_and_recovery | Where applicable, critique/revision/recovery responds to actual evidence; exploration can accept, reject, improve or abandon ideas without hindsight or forced agreement. |
| Critical | training_value | The trace provides a useful, coherent demonstration of the intended behavior; avoid padded boilerplate or reasoning that merely narrates an externally supplied answer. Assess this sample, not unsupported claims about corpus-wide novelty. |

Programmatic graph errors and diagnostics are supplied as evidence, not rechecked
by asking the LLM to tokenize or simulate tools. Advisory warnings are not
automatic disqualifiers. Each applicable qualification needs findings/evidence;
allow explicit not-applicable only where the criterion supports it. A missing
observation is not a fabricated pass. Exact aggregate-verdict and interrupted
review behavior still needs to be aligned with endpoint/context handling.

Confirmed customization: editable project-wide Core and Critical checklists;
there is no uneditable stock core. Ship initial criteria as defaults, retaining
stable criterion IDs separately from editable labels/text. Preserve exact checklist
version/hash/snapshot for old signoffs rather than rewriting their meaning when
project criteria change.

### Structured review signoffs

Confirmed response contract: the model returns a JSON object covering each item
in its checklist, with evidence explaining why it passes or fails. Fast covers
Core; Critical covers Core plus Critical-only criteria. Corresponding Repair &
Validate emits the same tier's signoff on its repaired source. Save the signoff
in trajectory metadata alongside the model that approved/denied the source.

Proposed model response shape:

```json
{
  "items": {
    "core.causal_evidence": {
      "status": "pass",
      "evidence": "The decision uses only the earlier visible tool result.",
      "event_ids": ["result-17", "reasoning-18"]
    }
  },
  "summary": "Concise overall assessment."
}
```

This is a single-item illustration; actual reports must contain all expected
criterion IDs. Evidence is a concise grounded justification, not merely a
boolean or an unreferenced confidence score. Permit not-applicable only where
the criterion definition allows it, with its own evidence. Intermediate requests
for more context are not final passing signoffs.

Programmatically validate complete item coverage, recognized IDs/statuses,
nonempty evidence and valid source references. Derive overall pass/fail from the
item results; any supplied overall verdict must agree. Do not promote on missing
or malformed JSON/items. Reuse the existing checklist result-schema machinery
where practical, replacing tick/sandbox references with stable graph evidence.

Application-owned signoff metadata proposal (the model does not self-certify
these identity/version fields): review ID, tier/operation, requested/actual model
and endpoint identity, timestamp, reviewed source revision/hash, checklist
version/hash, item results/evidence, derived verdict and stage transition. Record
approval and denial alike. Existing `metadata.evaluations` provides a starting
point; preserve a history rather than overwrite the last verdict. Exclude API
credentials and full duplicated provider prompts from portable signoff metadata.

Publish the signoff and stage transition atomically only if source/checklist
versions still match the reviewed inputs. Historical verdicts remain bound to
their reviewed version. Adding signoff/operational metadata must not itself alter
the reviewed behavioral-source digest.

Confirmed invalidation policy: mark stale, do not reset stage or mutate historical
signoffs. Candidate remains Candidate and Validated remains Validated after a
relevant manual source edit. Display that the current source no longer matches
its review. A stale indicator is distinct from a failed verdict.

Checklist edit scope:

| Change | Affected current stages |
| --- | --- |
| Core checklist changes | Candidate and Validated |
| Critical-only checklist changes | Validated only |
| Reviewed trajectory content changes | That trajectory's current review becomes stale; stage retained. |

Implementation proposal: track source revision/hash and separate Core/Critical
version/hash components in each signoff. Fast depends on Core; Critical depends
on Core and Critical-only definitions. Derive or index current stale state against
those fingerprints and record its reason; do not rewrite every old signoff on a
global checklist edit. Use stage/version indexes or generation counters to expose
affected sets without reading every source blob. A fresh applicable signoff can
clear its staleness only for the matching source/checklist versions.

Confirmed stale-review scheduling: always manual. Never auto-queue a review just
because source or checklist edits stale a signoff, even if endpoints are available
and automatic post-generation review is enabled. Expose stale filters/reasons
and explicit review actions without running them as a side effect of settings
changes. There is no opt-in automatic stale-refresh mode to implement.

Minimum-stage export selection is now defined under D11; stale-source export
handling remains open. Bookkeeping-only updates should not recursively stale
the signoff they just saved.

Implementation requirements/proposals:

- Separate content stage from job lifecycle: provider failure/cancellation is not
  a negative quality verdict. Keep Unfinished/Pending/Candidate as appropriate
  when a request cannot complete; journal the interruption and allow resumption.
- Bind review verdicts and checklist/model/prompt versions to exact source
  revisions. Recheck current source before publication to prevent stale verdicts.
- Store checklist-specific notes/evidence and reasons for promotion/demotion,
  distinguishing structural diagnostics, Fast findings and Critical findings.
- Treat tool-schema, numerical-drift and intentional-open diagnostics according
  to their advisory policies; do not convert warnings into automatic structural
  failure simply because a review model is enabled.
- Reviewers can inspect historical graph evidence, but must judge each decision
  against information available at that event's frontier. Hidden future facts
  do not justify a correct-looking trace. Actor prompts must not inherit these
  private review transcripts or verdict labels.
- Queue optional review jobs after completed source publication; share provider
  capacity through FIFO request dispatch without holding generation transactions.
  Both toggles off retains throughput-focused creation with Pending sources.

Confirmed structural-failure boundary: invalid event attempts use bounded local
retries first; exhaustion without valid output makes the trajectory Unsuccessful.
Repair routing, archive meaning and automatic/manual invocation are confirmed;
single-attempt verdict/context-limit policy is confirmed; next define graph
inspection/patch mechanics within that one attempt.

### Prompt layout and cache-aware dispatch

Confirmed goal: group prompts to improve cache-hit likelihood. Preserve exact
stable prefixes where requests have shared instructions, without relying on
provider caching as a correctness mechanism or exposing hidden context for reuse.

Proposed request layout:

1. Stable shared protocol/authoring instructions and safety against source-data
   prompt injection.
2. Stable role-specific instructions, output contract and project-visible tool
   definitions appropriate to that role. A tool's presence in project settings
   does not automatically mean it is available in the simulated scenario.
3. Stable serialization of any shared, permitted scenario/context prefix.
4. Dynamic causal window state, selected event task and scoped nudge.
5. Request-specific identifiers/diagnostic metadata only where needed; keep
   timestamps/random IDs and volatile fields out of reusable static prefixes.

Use deterministic schema, tool and JSON ordering, stable wording, and versioned
prompt templates. Avoid per-request enums containing newly generated IDs or
rewriting common instructions unnecessarily. Validate dynamic reference IDs
locally against the request's allowed context. Rebuild the visible prompt at each
frontier: cache reuse never justifies retaining closed/replaced/parallel content.

The current ModelSession.render_prompt concatenates profile/schema instructions
and JSON inputs; refactor into a shared request builder with explicit static and
dynamic components rather than leaving every Flow step to improvise ordering.
Separate provider completion cache from authoring context: a stateless request
with the correct prefix is not a rolling conversation retaining hidden text.

Confirmed scheduler overrides the earlier cache-affinity proposal: FIFO, one
queue entry per API request, not per trajectory or whole Flow session. A
trajectory needing three generation calls consumes three separate queue entries
as those calls become ready. No manual-request priority or cache-based queue
reordering. Improve cache hits through stable prompt prefixes/layout rather than
dispatch affinity. Respect dependencies, visibility and endpoint concurrency caps;
do not wait for all trajectories to reach the same phase. Confirmed FIFO scope:
one independent queue per endpoint, preserving the configured endpoint capacity.
Transport retries stay within the current logical API-request item until success
or configured retry exhaustion; do not requeue them at the tail. Separate Flow
calls/tool turns create new items. Exhaustion pauses that endpoint under the
existing policy without blocking the other endpoint queues.

Provider caching capabilities and routing vary; do not promise a hit rate or
specific OpenRouter behavior without verification. Record provider-reported
cached input tokens/cache usage when available, separately from estimates and
uncached tokens; absence of a cache field is unknown, not a measured miss. Expose
enough request/prefix grouping information to evaluate whether the layout helps.
Live provider behavior/price tests are separate from this design discussion.

Confirmed primary direction, clarified by the user: proposal-driven generation
through one shared Flow. The matrix defines sample parameters, not implementation;
LLMs and Flow still perform actual generation. A custom shared Flow may have
multiple branches selected by matrix/proposal parameters. Adding a competency
should not require a separate flow file. This corrects the earlier interpretation
that Flow might be replaced outright by matrix configuration.

Multi-user and idle-heartbeat support are required, not optional inclusion
decisions. Their implementation and compatibility with matrix parameters need
discussion before the shared branching Flow is finalized. Do not assume every
episode starts with one user request or ends with a user-facing answer.

Confirmed chatroom protocol: one shared chatroom window is the default instruction
and response mechanism for single-user and multi-user episodes. The model cannot
generate directly into this window; it uses the chat tool to append a message.
Users also enter messages through the external side of the chatroom. This
prevents direct model/user writers from overlapping in the same text stream.
The existing chat tool's content argument and message framing are a reference,
but its executable sandbox implementation is not retained for the graph workflow.

Proposed graph lowering for chat: model-generated chat request in its own window;
chatroom append is a separate externally delivered event linked to submission,
with default external/tool-result masking. Preserve speaker identity separately
from whether tokens are direct model output or external delivery. New chatroom
events depend on their predecessor in the same room as well as their causal
inputs, preserving same-window ancestry even with concurrent senders. No direct
model generation into a read-only chatroom should be emitted by the generator.

Confirmed chat helper: automatic chatroom entry creation is enabled by default
and can be unchecked for a specific custom outcome. User examples: censorship
of words or rejecting a chat request for sending messages too quickly. Disabled
automatic handling allows authors to provide transformed messages, explicit
failure results, or no chatroom append at all. The submitted request is not
assumed to guarantee successful delivery. Do not implement a new executable
moderation/rate-limit sandbox to support these authored scenarios.

Proposed UI: a per-chat-call automatic-entry checkbox, initially checked. The
helper creates an explicit graph event linked to the call and previous chatroom
event; manual mode exposes authored delivery/result events like other tools.
Record provenance linking auto-created entries to their source call, avoid
duplicate echoes on resume, and never overwrite a customized entry with a new
automatic echo. Exact edits/ownership behavior belongs to D07. Invalid/incomplete
raw requests remain authorable; automatic entry creation needs complete parsable
content or an advisory diagnostic/manual path, not a forced schema repair.

Assistant proposal: distinguish learning objective from interaction structure.
Competency flags choose the coordination behavior; independent proposal fields
describe participants, conversation/window topology, initiation trigger and
episode completion conditions. Avoid representing multi-user and idle as mutually
exclusive competency families: a multi-user project can also have a heartbeat
continuation. Validate compatible combinations in the matrix and route a shared
proposal-driven generator without author-maintained per-case flow files.

### Idle heartbeat and autonomous work

Confirmed definition: an implicit instruction to react without user input.
An external stimulus (startup, alarm, file watcher, long-running-agent update,
etc.) wakes the agent; it selects a response from that stimulus, standing
instructions and project state. Do not force every stimulus into a synthetic
user request or require one universal heartbeat-window message.

User examples to cover:

- Standing instructions to review files at certain times of day.
- An alarm prompting low-priority TODO work while users are offline.
- Checking progress of long-running subagents.
- Future planning or exploratory inspiration for a difficult problem.
- Reviewing long-horizon progress and reconsidering whether the current plan is
  still the best path.

The times/day and offline conditions are scenario facts/content, not new authored
delay fields or a scheduler to run in this session. The actual wake is an
external graph event and obeys the established sleep rule. Include required
standing instructions/task state as observable context; do not let hidden sampler
objectives substitute for information available to the model.

Prior design discussion: Selective Exploration and Masked Critique,
November 11 discussion. Its proposed pipeline covers stimulus/grounded evidence
-> idea -> critique -> revised idea -> re-evaluation -> useful action or discard.
Flawed proposals can be context-only masked events, with supported critique and
improved revisions supervised. Inspiration includes grounded external mechanisms
and cross-domain analogy, with no forced analogy when unhelpful. These remain
training hypotheses rather than demonstrated improvements. Here they inform the
authoring/generation capability; no new experiments are being scheduled.

Proposed matrix extensions separate entry stimulus, participant availability,
standing work/context and intended behavioral objective. Keep the shared Flow's
context/world construction, autonomous decision/exploration loop, authored
observations and verification/checkpoint steps reusable across these cases.
Existing matrix `justified_no_action` and wait/act contrasts remain relevant;
autonomous work should not be measured by activity volume alone. A user-facing
chat message is optional when appropriate, not a mandatory closing response.

Confirmed control-flow requirement: user-driven and autonomous phases can
alternate within one trajectory, including users returning while background work
is in progress. This is essential training coverage for state transitions at
inference, not a choice between mutually exclusive sample types.

Implementation proposal: one shared resumable decision/observation loop with
conditional steps selected from current proposal/world state and observed events.
Do not choose a permanent user/idle branch only once at sample initialization.
Preserve tasks, window IDs, observations and context through transitions rather
than restarting the scenario or clearing unrelated work. A phase change alone
does not automatically cancel ongoing work; any cancellation requires the
appropriate explicit graph behavior. Use real stimulus/presence/message evidence
instead of inventing a visible mode-switch token for the model.

The matrix should describe initial conditions and intended transition coverage,
not only a single static mode label. Keep requested transitions separate from
reviewed transitions actually instantiated in the graph, referenced by stable
event IDs. Exact fields and generation budgets remain part of D05/D06 design.

Confirmed sample boundary: windows may remain open when a trajectory ends.
Default diagnostics warn for unclosed reasoning/tool windows; an author can
explicitly mark each such window intentionally left open to suppress that warning.
This supports long-running calls and retained state without hiding accidental
omissions. No automatic close-all generation cleanup or inferred project-complete
label. Document/system/chatroom open state does not produce this particular
reasoning/tool warning.

Proposed persisted field: `intentionally_left_open` on stable window metadata,
default false, with a checkbox in the window inspector. It suppresses only the
end-of-sample open-window warning, not invalid dependencies, writes after EOS,
tool schema warnings, or other review findings. It does not itself keep a window
open, alter sleep, grant acceptance, or inject a cue into model context. Store the
author's declaration in source so every reviewer sees the same intention.

Sample completeness is distinct from full project completion. The shared Flow
can finish an episode at a coherent open-state checkpoint and records actual
outcome instead of fabricating final completion. No implementation authorized.

Preserve the exact versioned proposal JSON as an immutable input artifact:
configuration/version/hash, seed, proposal ID, world group, learning objective,
scenario facts, generation directive and requested metrics. Record overrides
and eligibility/constraint decisions supplied by the sampler. The adapter must
not reimplement or silently weaken sampler constraints.

Use an explicit Flow input schema and `${proposal...}` references. Keep `needs`,
explicit inputs, role instructions, schema validation, bounded repeat, retry and
saved prompt/checklist contracts. No legacy tag-flow adapters are required;
derive display tags from a proposal only as a projection.

Do not assume current small/medium/large templates fit the new proposal ontology.
Choose the replacement flow structure after inspecting the exact sampler files.
Keep scenario family, world group, template family and trajectory UUID distinct;
allocate split to the appropriate stable family/group before related variants.
The sampler's world_group_id alone does not establish semantic independence.

Separate sampling targets, generated/observed labels, review evidence and
compiled measurements. Preserve unknown/custom metadata on round trips and
patches. Link annotations to the source revision and stable evidence IDs.

## Revisions, jobs and transactions — proposed

Retain one local server/coordinator and SQLite WAL. Multiple browser/MCP clients
can use it; multiple independent worker-server processes are outside the initial
scope unless requested. Never hold a DB transaction during model/network work,
tool-call checking or token compilation.

Add immutable trajectory revisions plus a current-head pointer. Separate source
identity, annotation revision, job progress and project configuration versions.
Project change sequence supports listing refresh; it is not the conflict token
for edits to an unrelated trajectory. Use compare-and-swap on expected head
revision/hash to publish source changes.

Persist batches, jobs, step attempts and request/artifact references with indexed
status/ownership fields. Existing JSON snapshots may remain payloads during a
transition; a complete schema rewrite is unnecessary in the first PR. Large raw
responses must not be copied into every source revision or polling response.

Job states: queued, running, pausing, paused, retry-wait, completed, failed,
cancelled, interrupted, conflict. Separate creation completion from review and
training acceptance. Preserve the original proposal for a retry; drawing a fresh
proposal creates a new attempt with explicit lineage, not a silent retry.

Confirmed run snapshot: retain the instructions/configuration the run started
with until it finishes or is cancelled, regardless of later matrix/Flow edits.
This includes later trajectories admitted within the same run, not just those
already being generated. Freeze matrix/metric-target definitions, Flow/prompts,
model choices and checklist versions as applicable; future NEW runs use updates.
Preserve the original target/proposal snapshot per attempt. Credential secrets
remain outside source/snapshots and export artifacts. Existing checklist-change
staleness policy still applies to signoffs made against older checklist versions.

Confirmed batch accounting: requesting N trajectories means N distinct new
trajectory attempts, not N API calls or N passes. Generation/transport retries
stay within the same attempt and do not increment N or replace failed outcomes.
An attempt remains in the active lifecycle until detached; paused/attached work
is not a completed attempt. A normally finished N-attempt run yields N detached
trajectories, pass or fail, including failed-repair archives. Manual detachment
also ends that attempt without reattachment or automatic replacement. Cancelling
a run does not require starting previously unstarted attempts to fill the quota.

Publish a new source head, accepted step checkpoint, ownership transition and
change notification atomically. Failed/conflicting results remain inspectable
candidates; never overwrite a newer head. Commit idempotency is achievable;
exactly-once paid provider execution is not guaranteed after an ambiguous timeout.
If leases are used, fence commits against stale attempts.

Confirmed editing/automation ownership: a trajectory is locked for its entire
automatic pipeline, not just the current writing request. It remains locked
through generation, queued/running Fast/Critical review and any automatic repair.
Only pipeline completion (including enabled reviews) or manual detachment allows
editing. Pause/suspend is not sufficient to unlock; this supersedes the assistant's
earlier pause-before-edit proposal. Unrelated trajectories remain editable.

Detachment is a durable pipeline-level decision: no further automatic content/
stage/signoff updates, LLM dispatch, review or repair for that trajectory. New
user-created trajectories start detached. Stage, origin and attachment are
separate: detachment is not a quality verdict, loss change, or deletion of prior
work/reviews. Completed pipeline results can be edited without silently restarting
automation; stale-review changes still await manual action.

Implementation proposal: one automation owner/epoch per trajectory, persisted
with its queue jobs and request records. Detach transaction revokes that epoch,
removes/cancels queued steps, unlocks the source and retains the last committed
revision. Cancel in-flight calls where supported; fence late results at commit
so they cannot overwrite edits, append a signoff or change stage after detachment.
Every dispatch and publication checks ownership. Do not auto-resume detached
jobs after restart or endpoint reconnection. Job/billing diagnostics can remain
in the separate request ledger without mutating the detached trajectory.

Define pipeline completion from pending work, not stage alone: Pending can be done
when Fast is off; Candidate can be done when Critical is off; an Unsuccessful
source can still have a configured repair queued. Keep read-only inspection and
job visibility available during locks. UI, REST and MCP enforce the same rule.

Confirmed terminal handling: pipeline completion automatically detaches the
trajectory, releasing the lock and allowing manual actions. Failed-repair
abandonment also detaches at archived Unfinished with attempted edits retained.
Commit final stage/signoff/diagnostics and terminal detachment as one consistent
outcome; do not detach early between generation and enabled reviews/repairs.
Paused/interrupted work is not completed and must not become editable merely
because no request is currently in flight. Detached failures stay excluded from
normal automatic resume, including after server restart.

Confirmed manual-operation policy: review/repair can be manually triggered only
on detached trajectories. Attached sources cannot receive manual review/repair,
because it conflicts with the creation pipeline. An explicit manual operation
does not count as automatic activity and does not reattach the source. Temporarily
lock it while the requested operation runs, then leave detached. Its stage/signoff
changes are the explicitly requested result. Do not auto-chain further review or
repair because that manual operation changed its stage.

UI/REST/MCP eligibility checks use attachment plus active-operation ownership,
not stage alone. Reject a manual start while attached or while another operation
owns the detached source. Stage eligibility for each review/repair still applies.
Persist operation ownership and clear/recover abandoned locks after interruption
without launching new model work. Confirmed: no reattachment. A detached source
never rejoins an automatic generation pipeline, even through an explicit restart
button. New automated generation uses a new trajectory identity; lineage can
link it to the prior source/proposal if appropriate. Do not copy approval/current
signoff status to that newly generated source. Manual action never restarts the
old pipeline, and undo must not reactivate revoked automation ownership.

Confirmed Undo/Redo storage: browser cache, bounded by a user-selected number of
actions in Preferences. Do not persist the interactive undo stack in project
SQLite or keep one shared server-process stack for all clients. Keep automatic
worker checkpoints and source/review provenance separate from browser editing
actions. Clearing browser storage loses this local history, not the saved project.

Implementation proposal: IndexedDB for history payloads, with lightweight client
Preferences in existing browser storage. Key stacks by stable project identity
and trajectory UUID; persist forward/inverse source edits and expected heads.
Use the configured action limit to evict oldest actions and clear redo after a
new edit. Avoid counting every streamed provider token as a user action. Restore
history on browser reload when source compatibility permits, but never restore
automation ownership or silently replay model operations.

Undo/Redo submits a normal revision-guarded manual source edit, only when the
trajectory is editable. Validate its expected source head; an incompatible edit
from another tab/client requires conflict handling instead of overwriting it.
Treat behavioral Undo/Redo as source editing under stale-review policy; preserve
stages and historical signoffs. Per-trajectory history must not be reset by
unrelated background generation or review on other trajectories. Exact action
grouping and stale-history reconciliation can be verified during UI testing.

Technical conflict-handling default: per-trajectory expected revision/head hash
on each manual save, independent of unrelated project updates. On conflict,
preserve both the latest saved source and the user's browser draft, show the
differences and require explicit reconciliation/reload. Never silently discard
the draft or overwrite newer source. A conflict does not reattach automation or
spend model calls. This follows the agreed ownership/history invariants; exact
panel presentation is a UI implementation detail.

## Provider scheduling and recovery — proposed

Three separate limits: active trajectories; global in-flight requests; per-endpoint
and per-trajectory request limits. Share capacity across generation/review through
FIFO request queues, with no manual-work or cache-affinity priority. Bound queued work and
CPU-heavy tool/export tasks separately. Start conservatively; expose configuration
and measure throughput before raising defaults.

Confirmed concurrency controls: independent request caps for Generator, Fast
Review and Critical Review endpoints, plus a separate active-trajectory limit.
Review/repair on any one trajectory has one shared exclusive operation slot,
not separate counters that could allow one review plus one repair concurrently.
The slot covers the whole review or continuing repair session, including model
tool turns and pending result publication. Release only at completion/cancellation
under the attachment/operation rules. Different trajectories may use these
endpoints concurrently within endpoint limits.

Implementation: atomically acquire trajectory operation ownership before queueing
manual or automatic review/repair, enforce across REST/MCP/browser requests, and
record it durably so restart cannot dispatch competing sessions. A second start
reports the existing operation instead of creating duplicate paid work. Fast to
Critical and failed review to Repair are sequential pipeline transitions, not
overlapping jobs. Single-request endpoint permits are held for network calls,
while the trajectory operation lock spans the whole session. Confirmed: gain
generation throughput by parallelizing across trajectories, with one generation
request at a time per trajectory initially. Authored graph execution can still be
parallel; generation authoring order does not add graph dependencies. No overlap
of a trajectory's generation with its review/repair path.

Reuse current OpenAI-compatible request support for OpenRouter where capabilities
permit. Add explicit endpoint capability settings rather than silent schema or
model fallbacks. Snapshot requested model/settings and record returned model,
provider/request IDs and usage/cost when supplied. Label estimates and unknowns;
do not reconstruct historical cost from a current price.

Confirmed spending policy: no application run/batch dollar budget or automatic
spending-cap enforcement in this overhaul. Keep it as a possible future feature.
Users configure caps on their backend service/API key. Continue until the API
stops responding; mark the affected generation attempt paused and preserve its
checkpoint/committed events. Do not turn backend refusal/outage/quota exhaustion
into Unsuccessful, Rejected or failed repair merely because work could not finish.
No additional approval or dollar-budget setup is required to use an already
configured enabled endpoint.

Usage/cost observability where available can remain diagnostic; it is not a gate.
Distinguish model request limits/context exhaustion with the explicit adopted
repair failure policy from transport/API unavailability. Preserve outstanding
request/attempt provenance and stage when no actual model verdict was returned.
Pause is not detach or completion; attached source locks remain in force until
manual detach or eventual terminal pipeline completion.

Confirmed failure scope with retry clarification: first enter a bounded API-retry
phase using a configurable retry count. Hold new dispatch to the affected endpoint
while recovering; successful retry returns it to service, exhausted retries mark
it dead/paused. The requesting attempt and queued work remain preserved, without
quality demotion/detachment. Other configured endpoints may continue within limits.
Dead/paused is endpoint operational state, not a trajectory stage.

Implementation: persist endpoint pause reason/status and show affected jobs in
Generation Control. Already in-flight calls may complete normally and publish
under their valid ownership; do not dispatch follow-up calls to the paused
endpoint. Do not auto-substitute another model/endpoint or rotate keys to bypass
provider limits. Retry count is confirmed configurable. Confirmed post-exhaustion
policy: explicit manual resume only, no background probes or retry loop after
dead state. Persist the dead state across server restarts; credential/model/URL
editing alone is not a silent restart of paid work. Expose clear endpoint error
details and manual Resume controls with affected-job counts.

Implementation proposal: one coordinated recovery owner per endpoint, preventing
every queued trajectory from independently issuing the full retry budget. Preserve
request identity/diagnostics for each retry and use bounded backoff/jitter where
appropriate. Define retry count as additional tries after the initial request
and label it clearly. Context exhaustion, invalid source content and malformed
review output are not automatically endpoint-unavailability events; classify
them under their own established policies. Exact retryable HTTP statuses and
initial defaults can be specified with provider integration tests.

Retry transient transport/429/5xx failures with bounded exponential backoff,
jitter and Retry-After support. Keep semantic/schema correction attempts separate
from transport retries. Do not automatically retry authentication/configuration
errors or silently change model/provider policy. Keep credentials outside source,
durable job artifacts and exports.

Pause stops new dispatch and lets active requests finish; cancel aborts active
requests where possible, preserves artifacts and prevents late publication.
Confirmed restart policy: interrupted jobs wait for manual Resume even when
their endpoints are healthy. Preserve completed events/checkpoints and original
run instructions. Never automatically dispatch paid calls on restart or resume
detached work. Dead endpoint state also remains paused under its existing policy.

Confirmed interrupted-response policy: discard an incomplete API response and
retry generating that event from scratch under the original request inputs,
retaining earlier completed events. Do not persist partial-text diagnostics,
drafts or recovery artifacts for the incomplete response. Temporary live stream
preview is not committed source and is cleared on interruption. Keep only the
operational request state needed for retry/ownership; no stored response fragment.
In a repair session, previously completed/committed spot edits are still retained;
only the incomplete response is discarded. Intentional authored partial tool JSON
from a normally completed API response is a valid event, not transport interruption.

## UI, review and metrics — proposed

### Confirmed workspace, timeline and API direction

Keep the overall current UI/panes intact, adapting individual controls rather
than designing a new workspace. Remove sandbox UI and authored tick markers.
Retain the timeline's existing general presentation and scrubbing interaction.
Client Preferences selects rendering mode:

- Dependency graph (default): graph/block rendering, no tokenizer required.
- Tick history: select a tokenizer profile in Preferences and show the timeline
  corresponding to that tokenizer's compiled ticks, retaining the old visual
  timeline renderer. Tokenizer/profile definitions remain in Project Settings;
  Preferences stores only the client-side selection/reference.

Confirmed refresh: automatically recalculate Tick history after a trailing
debounce, default 0.5 seconds, configurable in client Preferences. Restart the
timer on each relevant edit so rapid typing/changes coalesce and calculation
waits for a quiet interval. Do not throttle on a fixed interval or schedule one
preview per keystroke. Reuse valid cached previews; cancel/supersede stale work
and never render an old source/profile result over a newer state. Graph mode
does not trigger this tokenizer-specific work.

Both modes are visual aids and do not change authored source, export profile,
compilation results or manipulation semantics. Replace old marker indicators
with dependency indicators on hover. Support dependency editing through both
searchable event selection and direct connection gestures, in either mode.
Gestures operate on event/window IDs and dependencies, never persist derived tick
coordinates. Broken references keep their one-click removal affordance.
Scrubbing already supplies before/after inspection in both modes; do not add a
duplicate preview panel/toggle. Keep full Source JSON as an advanced editor option
while forms remain the normal authoring UX. Special tokens remain bordered
uppercase blocks derived from structure, not manually authored placeholders.

Dashboard project/job reads and writes must go through REST. Browser-local
Preferences and Undo storage remain local as already agreed. REST supplies the
complete application command surface and shared validation/locking semantics.
MCP follows these capabilities where reasonable, prioritizing agent readability
and composable task-oriented tools over one-to-one endpoint completeness.
Provide a healthy set of focused Skill/guide files for project setup, graph and
dependency editing, windows, tool fragments/chat, review/repair, jobs/recovery,
bulk processing, metrics/search and export. New guides must use new source
contracts, not sandbox/tick-marker workflows. Return bounded summaries, stable
IDs, diagnostics and job/artifact references rather than dumping full datasets.

### Confirmed independent job UI

Large imports/exports and other long operations run as server-managed jobs.
The server may manage any number of jobs provided they do not conflict; all
existing per-trajectory locks, request queues, endpoint/worker limits remain.
Implementation interpretation: coordinate conflicting writes/resources, with
read-only snapshot inspection/export not requiring a global mutation lock.
Do not retain a single global current-job UI state. Each job has its own log,
progress, state and pause/cancel/resume controls; controls are requestable while
work runs and honor safe completion/cancellation boundaries and ownership.
Add a bottom status bar with job-reference/status indicators. Selecting a job
opens its full inspection dialog/panel, including its own logs and controls.
Keep other jobs running, preserve editor selection/drafts, and permit inspection
without stealing focus for background updates. Existing Review & Repair progress
popup/auto-close rules remain; completed warnings/results remain inspectable.
Use REST job/status/log resources with bounded paging/change notifications.
Job logs must not persist the incomplete API response text the user rejected.
Confirmed generation-job lifecycle: Pause retains attached attempts and makes
them resumable. Cancel is terminal: stop queued work, cancel active requests where
possible, prevent late publication, keep committed results and detach started
unfinished trajectories once writers settle. Cancelled jobs do not resume or
reattach those trajectories. Prior cancelled-work/no-quality-failure semantics
stand; not-yet-started attempt slots do not require creating replacement records.

Confirmed job log cache: Project Settings includes Clear Log Cache and an
optional max-size limit in MiB, default 1024. Evict oldest retained log data when
the cache exceeds its configured limit. Retained logs can be exported at any
time, including from an active job's current snapshot; support per-job inspection
and log export. No indefinite-retention promise: old entries may be evicted or
cleared even though the job's compact record/history remains inspectable.
Implementation choice: segment/page logs per job and evict oldest retained
segments across jobs to bound cache size while keeping recent tails usable.
Cache eviction/clear affects log data only, never trajectory source, job status,
checkpoints/frozen instructions, ownership, review signoffs or Undo history.
Running jobs continue appending fresh logs after a clear; signal truncated/cleared
history in the log viewer rather than inventing missing entries. Export captures
a stable log snapshot and does not promise recovery of already evicted entries.
Log export artifacts use the same temporary /tmp delivery mechanism.

### Confirmed temporary export delivery

Browser exports may bundle multiple shards into a ZIP; APIs can expose individual
artifacts too. ALL exported shards (raw JSONL or compiled KTREMIX), the manifest
and ZIP must be created as temporary files under /tmp. Use a unique job-owned
temporary directory and unique temporary filenames; do not use persistent project
directories for generated export artifacts or collide across concurrent jobs.
Publish download references only to complete artifacts. Cancellation/failure
cleans partial outputs after active writers/download readers have settled.
Confirmed successful-artifact retention: expire after 24 hours. Do not rely on
OS /tmp cleanup, which varies and may also remove files earlier. Clean only
application-owned temporary artifacts, with periodic checks while running and
startup cleanup of overdue artifacts; do not delete files still being written
or actively served. Proposed age basis is export completion time. Expired/missing
artifacts are reported as unavailable without changing completed-job results or
its retained log/status records. ZIP is
delivery packaging only; constituent compiled shards remain ordinary KTREMIX
files for direct training after extraction.

Confirmed dataset-wide bulk processing: Edit > Bulk Processing operates on ALL
entries matching the current dataset search/filter, across pagination, not just
rendered/selected rows. Required examples: Set Stage and Set Split, including
Auto. Searching @stage:validated then Set Stage > Ready updates matching
Validated trajectories to Ready. Confirmed first implementation actions:
Set Stage, Set Split (including Auto), Calculate Missing Metrics, Delete, and
Review & Repair. The user replaced separate Review/Attempt Repair menu actions
with one Review & Repair configuration popup. Offer standard stage-based review,
Force Fast Review, Force Critical Review, and an enable/disable automatic-repair
option. Implement the review choice as one mutually exclusive mode. After Start,
show progress; close the popup when all requested reviews/repairs are finished.
Retain warnings/results outside the popup so auto-close does not hide skipped or
failed entries. A paused attempt is not finished under the existing endpoint
policy. Review & Repair keeps detached-only ownership, one review/repair operation
per trajectory and no reattachment. Enabling repair explicitly authorizes that
repair as part of this manual operation; it does not resume generation or imply
other unrequested follow-ups.
Confirmed standard routing: Pending gets Fast Review only (a pass stops at
Candidate; do not immediately run Critical). Candidate gets Critical Review.
Validated and Ready are skipped, even for an explicit bulk request. Unfinished
remains skipped as incomplete; Unsuccessful/Rejected retain their normal
corresponding-tier repair eligibility when repair is enabled. Normal verdict,
retry-exhaustion and repair stage transitions apply; do not introduce special
re-review routes for otherwise ineligible stages.
Confirmed forced routing: Force Fast/Critical ignores ALL stage restrictions,
including prior Fast-pass requirements and Unfinished/Validated/Ready exclusions.
Locks and manual ownership rules still apply; force does not detach or permit
concurrent operations. Stage bypass is not a bypass of structural validation or
endpoint availability. User confirmed: successful forced review keeps the higher
of the current stage and that tier's normal success destination (Candidate for
Fast; Validated for Critical). Thus Ready stays Ready on a pass. Failure follows
normal failure/repair transitions; do not restore a pre-failure higher stage
merely because a subsequent repair succeeds. A Fast pass does not fabricate a
Critical signoff or clear unrelated Critical staleness.
Bulk dispatch
must obey the same endpoint concurrency/retry policies as individual actions.
Delete confirmation is an assistant UI proposal, not yet explicitly confirmed.

Implementation choices: show the matched count/action, capture a server-side
snapshot of matching UUIDs/revisions before applying it, and do not re-run a
changing search after each update. Use bounded transactional batches and
per-trajectory revision checks, progress and changed/skipped/failed counts.
User confirmed: skip locked entries and warn that they were skipped; never
detach jobs automatically or bypass locks. Also skip/report concurrently changed
or otherwise ineligible entries under the proposed revision/eligibility checks.
Stage/split
updates change optional Remix metadata, not graph content or compiled behavior;
preserve review history and never manufacture signoffs when setting Ready.
User confirmed: one bulk operation counts as ONE Undo action covering all entries
it actually changed. Undo/Redo must use changed IDs/prior values rather than
re-running the search, subject to the agreed browser-cache history policy and
current lock/revision checks. Do not allow undo to overwrite intervening edits.
User confirmed: Undo restores previous trajectory state; Redo restores the saved
result without calling the model again. Store only reversible deltas for entries
actually changed, not snapshots of the entire matching set or unchanged text.
Capture changed fields/events (and values needed for inverse/forward patches),
omit skipped/unchanged entries, and group the actual changes into one Undo action.
For deletion/creation, restoring the affected record necessarily needs its value;
this does not justify caching other unaffected records. Credits are not refunded.
Confirmed cancellation/Undo timing: Cancel stops queued work and cancels active
requests where possible. Retain already committed results; cancelled work is not
a quality failure. Revoke the operation's right to publish additional results so
late responses cannot change trajectories after cancellation. Keep locks until
each active writer has settled/cannot publish, then release them. This does not
refund credits or imply the provider stopped processing an already-sent request.
The batch's one Undo action becomes available only after normal completion or
cancellation has settled, grouping actual completed deltas. Undo must not race
ongoing writes. Progress/reporting distinguishes completed, skipped, failed and
cancelled entries; an endpoint-paused operation is not a completed batch.

Confirmed missing-metrics behavior: missing metadata means metrics unavailable;
do not calculate automatically when opening/importing a trajectory. Provide an
explicit button to attempt calculation of unknown metrics where possible. Keep
metrics that cannot be determined unavailable; never invent values or confuse
sampler targets with measurements. Authoring-time calculations must respect the
export-only compilation boundary; token/compiler-dependent metrics remain
unavailable until a suitable export result exists.

Confirmed metric catalog/provenance: initial definitions follow the sampler
matrix and custom definitions are supported in Project Settings. Keep the sampler
and metric definitions closely linked, not separately maintained registries.
User clarified the edit synchronization concerns the SAMPLER MATRIX, not
trajectory editing. Matrix edits update corresponding metric definitions/target
configuration for future trajectories. Existing trajectories retain their
original sampled targets; never rewrite historical targets to fit a new matrix.
The later confirmed run-snapshot rule further specifies that trajectories started
later within an existing run still use that run's original matrix; updates apply
to trajectories in future new runs.
Requested target metrics and measured values remain distinct; do not substitute
updated targets for measurements or infer automatic trajectory recalculation.
Record calculation/annotation/export origin and source revision on actual metric
values; stale results must be distinguishable after edits. Any updates retain
the no-authoring-compilation rule and explicit handling of unknown metrics.

Reuse the existing metrics panel rather than creating a duplicate. Repository
inspection confirms renderMetrics/loadMetrics already expose population filters,
distributions, coverage and unavailable metrics. User permits a search bar in
that panel; chosen UI direction is to integrate the shared dataset query syntax
with its existing population selection and aggregation. Exact filter interaction
should be specified while updating that existing panel, not a new dashboard.

User delegated scenario-family grouping to assistant judgement. Chosen policy:
variants of the same underlying scenario (rewrites, alternate outcomes, wording
variants) share a stable family ID and split. Repairs preserve family identity.
New independent scenarios get distinct families; a shared domain/template or a
top-level sampler run seed alone does not make unrelated proposals one family.
Keep world/template labels distinct from scenario-family identity. Generation
assigns lineage explicitly; do not assume semantic similarity detection or LLM
grouping is available. This refines the prior split-integrity policy.

Possible future idea only: an LLM could assist with semantic tagging to help
determine unknown metrics. User explicitly marked this as a maybe, not confirmed
scope. No default model calls, endpoint requirements or implementation commitment.

- Confirmed UI separation: client Preferences and Project Settings are distinct
  sections/panels. Client Preferences includes display/editor behavior and Undo
  history limits, stored in client storage; no token-insertion-mode setting. Saving a
  preference must not call a project-write API or increment its revision.
- Current Preferences mixes local display/edit controls with project-backed
  default split and model API retry count (`static/app.js`, preferences submit
  handler). Move project-backed settings to Project Settings. The current
  auto-compile preference must not apply to Graph mode; Tick history gets its
  own preview refresh/cache handling. Source validation/autosave debounce stays
  client behavior. Runtime credentials retain
  their existing separate handling and are not made durable by this UI split.
- Timeline graph/tick-history rendering and hover dependencies follow the
  confirmed dual-mode policy above. Remove authored tick markers, not the
  opt-in tokenizer-derived tick-history visualization.
- Generation panel lists jobs/trajectories, status, progress, costs and per-job /
  batch controls. Following a job is explicit; background progress never changes
  the user's selection.
- Use UUIDs/revisions in APIs instead of mutable row indices. Update MCP, UI,
  guides and tests together; do not retain legacy-format compatibility handlers.
- Add a bounded change feed and SSE (or conditional cursor polling) with reconnect
  cursor and full-resync fallback. Notifications invalidate views; fetch source
  only for the selected changed trajectory. Coalesce transient progress updates.
- Review consumes graph/window history, requirements and recorded tool outcomes;
  evidence uses block/revision IDs, with bounded continuation. Source validation,
  semantic review and export validation report different guarantees.
- Source metrics cover proposal distribution, accepted behavior labels, families,
  splits, graph structure and generation costs without compilation. Graph width
  is not token-level peak concurrency.
- Exact token/NOOP/context/timing measurements exist only after export and carry
  source revision + tokenizer/profile/compiler identity. Missing/stale data stays
  missing/stale. The existing compile-on-refresh metrics action must change.
- Confirmed: adaptive coverage-driven sampling is deferred. Keep the sampler's
  existing Monte Carlo approach and manual pinning to skew generation. Metrics
  report coverage/drift; do not automatically alter sampling probabilities.

## Export — proposed

Confirmed Export menu:

| Option | Required behavior |
| --- | --- |
| Minimum stage | Ready, Validated, Candidate or Pending; Ready default. Include selected stage and higher nonnegative stages. |
| Trim metadata | Optional; when enabled choose Remove all metadata, Keep measured metrics only, or Keep all metrics (sampler targets + measured). All retain required source/training/decoding fields. Carry forward the keep-metrics default as Keep all metrics (assistant interpretation of the expanded choices). |
| Include stale trajectories | Configurable, enabled by default. Independent of stage; never refreshes signoffs or changes source/stage. |
| Tokenizer | Compiled export selects a tokenizer profile from a dropdown. Profiles and their per-tokenizer special-token mappings live in Project Settings, not Preferences. Raw export requires no tokenizer. |
| Exclude trajectories with missing split | Disabled by default. If off, resolve missing splits automatically using the project's configured split distribution. Explicit Auto is a valid mode, not missing metadata. |
| Output kind | Raw authored sources or compiled trajectories. |
| Shard size | Number of trajectories per output file, not token chunks, rows, events or ticks. |
| Raw format | .jsonl, one authored trajectory per line; no token compilation. |
| Compiled format | .ktremix; public 1.0 draft in docs/KTREMIX.md, pending review/freeze. |

The current exporter emits kidra-token-chunks.jsonl, hardcodes Ready-only filtering,
and uses ticks_per_chunk. Adapt both HTTP and CLI export to the new options.
The checkout originally had no .ktremix format implementation/specification.
User explicitly authorized repository docs/examples and a public draft PR for
the format and Python reference sampler, as an exception to the broader
implementation hold. The full planning document remains local and outside that PR.
Published draft: https://github.com/TheDudeFromCI/trajectory-remix/pull/70
Branch: feat/ktremix-format-spec. Current commit: 6099ae913f92fbf5bd533d35fbf0de0705af0c7b.

Public draft: fixed 128-byte header, JSON manifest, 64-byte sample index,
independently compressed whole trajectories (raw/LZ4/zlib), aligned typed columns,
explicit causal frontiers/window lifetimes, CRCs and compatibility identities.
Default LZ4 falls back to raw when compression does not shrink the record.
Reference samples uniformly over all trajectory indices across unequal shards;
supports disjoint distributed epochs, bounded mmap handles, and full verification.
Synthetic fixture writer is not the authoring compiler or production exporter.
18 reference tests pass; reproducible warm-cache loader benchmark is documented
with synthetic-workload limits. Review/freeze the format before integration;
compare future compiler outputs against the existing attention predicate.
The subsequently authorized training demo is documented in
docs/KTREMIX-TRAINING-DEMO.md: current replay compiler plus fixture-specific
progressive/sleep lowering, compressed sampling, two-layer shared-KV training,
teacher-forced validation and teacher-free timing/closure checks. Five additional
training tests and the explicit 3,000-step --check convergence run pass. This
exception does not authorize implementing the broader overhaul yet.

Implementation proposal: use one snapshot/selection policy across UI, MCP and
CLI, count eligible trajectories before starting and show the selection/options.
Confirmed split behavior: add Auto alongside explicit Train/Development/Test.
Missing split warns in the dashboard independently of all export settings.
Explicit Auto is intentional and must not receive a missing-split warning or be
excluded by Exclude trajectories with missing split. Missing values are eligible
for automatic assignment by default; the exclusion toggle defaults off.
Assign Auto and included missing-split trajectories randomly so the resulting
export population approaches the project's configured split ratios as closely
as possible, accounting for explicitly assigned members. Explicit assignments
stay fixed. Project Settings must expose/validate the distribution; a single
default split can be represented as a one-hot distribution.

User delegated family-conflict policy to assistant judgement. Chosen policy:
known related family/group members share a resolved split. One explicit split
within a group fixes its Auto/missing members to that split. Conflicting explicit
splits in a known group block export and report the affected IDs; never silently
move explicit assignments or split a known group to improve ratios. Group
integrity takes precedence over an exact ratio; show actual versus requested
counts when exact balance is infeasible. The exact grouping relation remains
D06 work; absence of group metadata is not proof of semantic independence.

Implementation choices: seeded randomized allocation of whole assignment units,
account for existing explicit counts, use bounded balancing/local improvement,
record assignment seed/ratios and resolved file split in the export manifest.
Resolve against the export snapshot before metadata trimming/shard chunking;
known explicit group assignments elsewhere in the project must also be checked
to avoid assigning a related subset differently. Keep Auto/missing source
metadata unchanged by exporting; bulk Set Split is the explicit way to persist a
mode. These export-only/reproducibility mechanics are assistant design choices.

Partition by resolved split before sharding. Train/test and other splits use
separate files; never mix them to fill a shard. Retain split identity in shard
manifests/filenames even after per-trajectory metadata removal. Group IDs remain
optional metadata, not compiler inputs. A family can span multiple files within
one split; no single-shard-per-family restriction is imposed.
Keep each trajectory complete inside its shard, even when internally split into
compiled chunks. Assign deterministic shard names/order; final shard may contain
fewer than the configured count. Stage to disk and publish complete artifacts
without buffering the full dataset in memory. Preserve snapshot identities even
if source edits happen during export.

Metadata trimming is an export projection only, never a project edit. Metadata
is entirely optional under the confirmed primary-schema rule above; remove the
object altogether in Remove all mode. Required trajectory data must live outside
it, rather than being exceptions hidden inside a supposedly optional object. User
expanded the original two choices to three trimming modes: remove all optional
metadata; keep measured metrics only; or keep all metrics, including sampler
targets and measured trajectory metrics in separate fields. The untrimmed export
remains available by disabling trimming. All three trimming modes remove raw
model responses, generation/review journals, verbose archived attempts and
signoff/checklist text. Metric retention does not recalculate metrics or promote
proposal targets into measured values. The exact metrics payload still belongs to D06.
Keep graph content/dependencies,
window identities/headers, source/speaker facts required for rendering, event
masks/losses, token IDs, causal/lifetime/slot data, tokenizer/profile identity and
format compatibility fields. These are required structural/decoding data, not
optional metadata, even in Remove all mode. Carry forward the user's earlier
Preserve metrics default as Keep all metrics; this mapping is the assistant's
stated interpretation, not a separately confirmed preference between the two
metric modes. Exact full/metrics payload schemas need review. Never export
API keys or credentials in either mode.

Raw sources may preserve editable structural issues without invoking compilation;
compiled exports require successful lowering/compilation for selected trajectories.
The configured stage threshold is selection policy, not a claim that every sample
is valid or its review is current. User confirmed inclusion of stale trajectories
is an Export setting, enabled by default. Shard defaults and trim/output defaults
other than the explicit Ready threshold can be chosen in the concrete UI spec.

Pin a manifest of exact source revisions, annotation/review state and export
profile (tokenizer, control spellings, slot policy, lowering/compiler versions).
Validate graph structure and protocol/lifecycle constraints first; no semantic
tool-observation freshness gate is added after the user rejected contamination
tracking. Lower block/milestone
constraints to the existing external-first token schedule; only this export path
emits training arrays/artifacts. Tick history may reuse the scheduler read-only.
Use existing `kidra-token-chunk/1` semantics/reader as a baseline where compatible,
but version .ktremix container and any changed payload contract explicitly;
do not merely change an extension and imply a new format is defined.

Keep a source map from block IDs/text spans to compiled rows for diagnostics and
measurements. Compute exact metrics as a by-product. Stage artifacts and publish
only after the selected export's validation succeeds; cancellation/failure leaves
no apparently complete dataset. Store checksums and manifest provenance.

Existing compiler tool execution needs an export-specific path that consumes
recorded outcomes instead of rerunning callbacks. Preserve attention, closures,
control targets, replacements and observed-context semantics.

Confirmed tokenizer setup: projects can be created and authored/generated/reviewed
without any training tokenizer. Graph trajectories are tokenizer agnostic and
must compile for any configured supported tokenizer without source changes.
Project Settings stores multiple tokenizer profiles, each with its own special
token mapping; compiled Export selects one via dropdown. Raw export does not
require a profile. This supersedes a single mandatory tokenizer at project init.
Normal Graph-mode authoring, generation and review must not load the selected
training tokenizer or invoke its compiler; user-requested Tick history alone
may load a client-selected profile for read-only timeline scheduling. This does
not select the export profile. Provider request budgeting stays separate.

Implementation choices: stable named profile IDs, project-owned tokenizer
definitions plus logical-control mappings, validation at setup/export, and exact
profile/tokenizer fingerprints in exported manifests and compiled metric keys.
Changing a profile affects future exports/caches, never rewrites graph source or
mutates/deletes already published immutable shards. Previous shards remain
usable with their recorded tokenizer/profile contract.

Confirmed final representation: source uses logical structural fields/flags and
dedicated header/chat handlers, translated to the selected tokenizer's actual
special tokens at export. No manually authored token placeholders are needed.
Reject a tokenizer profile missing required special tokens (including the
BOS/EOS/NOOP controls); do not add tokens, grow its vocabulary or repair the
tokenizer automatically. Report missing/incompatible mappings clearly during
profile setup/export. Tokenizer-agnostic source means any compatible profile,
not accepting a tokenizer that violates required control-token contracts.

## Format boundary and validation gates

Confirmed: ignore legacy formats. User explained that the entire generation
pipeline is being rewritten and the existing trajectory inventory is too small
to justify backward compatibility. No legacy project readers, read-only mode,
conversion tools, old Flow adapters, sandbox conversion or checkpoint migration
are required. Reject unsupported older inputs with a clear format/version error.
This replaces the earlier proposed migration/legacy preservation requirements;
it does not instruct deleting user files during this planning discussion.
Reuse relevant compiler/attention predicates as regression references without
committing to legacy source support in the new application. The public training
demo's temporary fixture adapter does not impose a legacy product requirement.

Required checks before default switch:

- Assert Graph-mode editing, generation, tool checks, review and metrics never
  invoke token compilation/scheduling. Only explicit Tick history requests and
  compiled export may enter tokenizer-specific scheduling; preview emits no
  dataset/source/metric changes, while export emits training artifacts.
- Graph cycles/references, same-window ordering, partial observations, lifecycle,
  cancellations, simultaneous outputs and future-evidence rejection.
- Multiple tokenizer exports of unchanged graph source; mask-boundary diagnostics
  and unchanged compiled attention/NOOP/closure semantics on matched fixtures.
- Project tool-schema form editing, Form/Raw event round trips, partial request
  preservation, advisory schema warnings, authored output ordering/cancellation,
  review invalidation after edits and explicit rejection of unsupported formats.
- Concurrent writers to different trajectories; stale same-trajectory commits;
  transactional source+checkpoint publication and crash/restart recovery.
- Provider retry classification, partial stream interruption, fairness, shared
  limits, no duplicate acceptance and bounded cancellation latency.
- Metadata round trips, family split integrity, frozen revision manifests and
  source-versus-export metric population accounting.
- UI selection/draft protection, reconnect recovery, job controls and bounded
  payload/memory behavior with dozens of changing samples.
- New source/export versions are explicit; unsupported formats fail clearly.

Confirmed validation placement: target 25,000 stored trajectories and 20 active
trajectory pipelines. User will run ALL load/throughput tests on their own
computer. Do not execute throughput tests, scale runs, performance benchmarks or
full training throughput experiments in this assistant workspace or CI. Prepare
the harness and reproducible instructions/results schema only. Existing earlier
benchmark observations predate this restriction; do not rerun them here.
CI/assistant may run small deterministic correctness tests, including queue/lock
and one-step optimizer checks, but no timed throughput/load gates. User-machine
validation reports graph operations, export, sampling/training, UI responsiveness,
memory and DB write volume without promising unmeasured speed targets.

Confirmed live integration acceptance: user will exercise real generation/review/
repair with a LOCAL LLM, not a hosted-provider requirement. Prepare endpoint-
configurable smoke scenarios for ordinary chat, multi-user, autonomous wakeups,
tools, reviews and repairs. Offline mocks remain useful for deterministic CI;
local-LLM execution and throughput runs are performed by the user.

## Proposed implementation sequence

User delegated PR strategy and first-milestone sequencing. Chosen approach:
focused runnable draft PRs, not a monolithic rewrite. The first usable milestone
is manual graph authoring through REST/UI/MCP basics, timeline views, raw and
compiled export, and integration of the existing 50k training demo with the real
new compiler. Automated sampler/Flow generation/review comes after that core.
Foundation-only PRs can add tested contracts without immediately switching the
UI; the usable milestone must be coherent end to end. The three sampler-matrix
UI design branches remain a separate experiment sharing one backend.
Concrete contracts/PR boundaries are being consolidated in IMPLEMENTATION-CONTRACTS.md.

1. Settle graph/block/tool semantics and the new format boundary; freeze representative
   source and training-contract fixtures and acceptance criteria.
2. Add versioned graph models, source validation, immutable revision primitives
   and metadata contracts, without legacy-format adapters.
3. Implement schema-only tools/authored observations and a graph fixture end to end;
   implement export-only lowering and semantic parity checks.
4. Replace builder, generation/review views and editing commands for graph source.
   Remove implicit non-export compilation from generation/review/metrics; add
   only the explicitly requested read-only Tick history scheduling path.
5. Integrate the exact sampler artifacts and one complete generation/review/export
   flow, preserving metadata and family assignments.
6. Extract isolated per-job Flow contexts and provider service; add durable queue,
   atomic publication, fair concurrency and recovery.
7. Adapt existing UI with dual-mode timeline and multi-job status/dialogs; use
   REST throughout, agent-focused MCP/Skills and temporary import/export jobs.
8. Run small correctness/export checks; give user the 25k/20 scale harness and
   local-LLM suite to run on their machine, then retire unused
   legacy authoring paths without a backward-compatibility layer.

PR boundaries must yield coherent reviewable behavior; the items above are
dependency stages, not a requirement for one PR per item. Keep the design document
current rather than letting a long-lived draft implementation become the spec.

## Active discussion register

Statuses: confirmed, open, proposed, deferred. Only explicit user decisions close
an open item. Keep IDs stable across conversation turns.

| ID | Status | Topic | Remaining decision / proposed starting point |
| --- | --- | --- | --- |
| D01 | Confirmed | Architecture boundary | Tokenizer-agnostic graph authoring; training artifact compilation on export; explicit opt-in Tick history may tokenize/schedule for read-only visualization. Generation/review/metrics stay compile-free. Existing token execution contract retained. |
| D02 | Confirmed | Blocks and scheduling | External input→same-tick generation; agent completion→next tick. Source-dependent headers/framing. External-chain checkbox selects immediate (default) or next input tick. Same-tick predictions see closing window's pre-EOS content, never current EOS; absent next tick. Prior graph/collision/sleep rules stand. Concrete lowering implementation/fixtures tracked in D11. |
| D03 | Confirmed | Synthetic tools | Schema-only tools/authored free-form outputs, fragment framing, cancellation and schema-edit warnings settled. Event edits do not mark other events contaminated or add reminders; consistency is for authors/reviewers. Structural errors and saved review-signoff staleness remain separate. Concrete schema work is D04. |
| D04 | Engineering draft ready | Source schema and supervision | Product rules confirmed; proposed source variants, primary/metadata boundary, IDs, errors and framing are in IMPLEMENTATION-CONTRACTS.md §2–4. Freeze model/reference grammar in foundation PR after plan review. |
| D05 | Partly confirmed | Sampler and shared Flow | Review/repair policies stand. Sampler closely owns metric definitions; Monte Carlo and manual pinning retained, adaptive balancing deferred. Tolerances per metric, no universal default. Remaining: tool-result tests, matrix extensions and specific per-metric defaults. |
| D06 | Partly confirmed | Families and dataset metrics | Sampler matrix edits synchronize metric definitions for future generation; existing targets retained. Catalog/provenance, panel reuse, family policy and prior split/missing-metric rules stand. Adaptive balancing deferred; LLM tagging only maybe. Remaining: concrete definitions, ratios, source/export metrics and manifests. |
| D07 | Engineering draft ready | Jobs, revisions and signoffs | Ownership, revisions/CAS, immutable commits, job records and delta Undo proposal consolidated in IMPLEMENTATION-CONTRACTS.md §5–7. Policies settled; implementation/functional validation pending. |
| D08 | Policy confirmed; technical draft pending | Provider policy and scale | Frozen run instructions; independent FIFO per endpoint, retries stay in current item until exhaustion; concurrency across trajectories; discard interrupted responses; manual restart resume; fixed attempt count excludes retries. Earlier caps/dead-endpoint policies stand. Remaining: retry defaults/classification, concrete queue/recovery schema/routing and usage metrics. |
| D09 | Policy confirmed; technical draft pending | Authoring/review interface | Existing UI, dual timeline, 0.5s configurable trailing preview debounce; REST dashboard, agent-focused MCP/Skills; independent job logs/dialogs/status bar. Pause resumable; Cancel terminal/detaches. Log cache limit optional/default1024MiB, oldest eviction, clear/export controls. Prior bulk rules stand. Remaining: concrete UI/API/job/patch/change-feed implementation and design review. |
| D10 | Confirmed | Format boundary and project setup | No legacy compatibility. Graph-mode authoring/generation/review/raw export need no tokenizer; compiled export and opt-in Tick history select profiles independently. Profile definitions in Project Settings, preview choice in client Preferences. Reject missing special tokens without vocabulary changes. |
| D11 | Partly confirmed | Export | Existing selection/trim policies stand. Background jobs; shards/ZIP in /tmp with 24h expiry and failure/cancel cleanup. Preview profile independent of export. Public draft PR #70. Remaining: format freeze, metric payload, initial trim enablement, concrete lowering/manifests and production integration. |
| D12 | Plan approved; handed off | Focused PRs/manual milestone first; contracts and task briefs prepared. Scale target25k/20 runs ONLY on user's computer; no throughput here/CI; live local-LLM checks by user. Remaining: assigned implementation, correctness and user validation. |

Special-token authoring is settled: automatic structural controls, no manual
tokens/placeholders. Concurrency/recovery policy is now confirmed: frozen run
instructions, FIFO request queue, one generation call per trajectory, discard
partial responses, manual restart resume and fixed trajectory-attempt counts.
FIFO is per endpoint; retries remain in the current item until exhausted. Cross-event
semantic invalidation is rejected; D03 and D10 are settled.
Delivery choices/contracts are approved. The user requested handoffs to avoid
context/cache overhead. docs/handoffs/README.md indexes seven implementation work
packages, existing PR #70 notes and three sampler-UI alternatives; CONTEXT.md is
the compact stable prefix. No overhaul implementation code or benchmark/LLM run
was performed while preparing the packet. Next: assigned fresh task implementation;
restore exact sampler inputs before 04. Then fixture checks, UI design review and
user-run local-LLM/25k/20 validation. Do not reopen settled policy/field names.
Preserve the three
previously requested matrix-tab design branches and planned review iterations.
D11 public format still needs review/freeze;
metadata trim groups are settled. D06 still needs concrete metrics definitions/payload.
Defer numeric shard defaults. Provider implementation
defaults can be drafted/tested from settled policy without speculative user
questions about routine scheduling mechanics.
Updated user preference: use larger numbered question batches grouped by
subsystem; this supersedes the earlier small-batch preference. Do not reopen
confirmed choices or ask the user to resolve the entire register at once.
Current conversation ends at handoff preparation. Fresh conversations implement
only their assigned package and create focused draft PRs; no automatic merge or
starting the entire remaining plan merely because the packet exists.
