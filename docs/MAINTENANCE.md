# Maintenance sweeps for agents

Prioritize data loss, incorrect training behavior, broken workflows and misleading
product documentation. This checklist does not assert that checks have passed.
Read the current user request, applicable AGENTS.md, recent changes and existing
handoff records. Preserve unrelated work and resolve concrete defects before
speculative refactoring. The current request determines which model calls or
workloads are authorized; ordinary tests must not invoke configured paid models.

## Review map

Paths below are relative to `trajectory_remix/`.

| Area | Entry points | Review focus |
| --- | --- | --- |
| Source and edits | `graph/models.py`, `validation.py`, `commands.py`, `storage.py` within `graph/` | Strict DTOs, structural diagnostics, stable IDs, immutable snapshots and source CAS |
| Compiler/export | `graph/compiler.py`, `graph/export.py`, `graph/ktremix.py` | Causal visibility, lifecycle/masks, metadata projection, split families and leased artifacts |
| Generation/review | `graph/generation.py`, `graph/draft_assembly.py`, `graph/compact_spec.py`, `graph/assembly_fix.py`, `graph/reviews.py`, `graph/repair_policy.py`, `graph/provider.py` | Frozen inputs and prompts, Draft/Assembly/fix stages, checklist evidence, stage transitions and repair rounds |
| Prompts | `graph/draft_assembly.yaml`, `graph/prompt_defaults.py`, `graph/prompt_migration.py`, `graph/prompts_api.py`, `static/prompt-settings.js`, `static/checklist-settings.js` | Packaged defaults, per-project `settings.prompts`, the one-time Flow prompt migration |
| Durable work | `graph/jobs.py`, `graph/bulk.py`, `graph/job_api.py` | Ownership, FIFO, retry slots, fencing, settlement and changed-only Undo |
| REST/MCP | `graph/studio_api.py`, `graph/studio.py`, `graph/agent.py`, `mcp_server.py` | Shared services, loopback authentication, bounded outputs and lifecycle parity |
| Dashboard | `graph.js`, `jobs-view.js`, `metrics-view.js`, `window-render.js`, `ui-kit.js`, `views.js`, `shell.js`, `layout.js`, `graph.html`, `style.css`, `graph.css`, `project-settings.js`, `sampler.js`, `usage-view.js` within `static/` | Discoverability, real layout, dirty drafts, clean live updates, flicker-free polling, conflicts and accessible controls |
| Sampler/metrics | `graph/sampling.py`, `graph/metrics.py` | Supplied sampler fidelity, immutable proposals, unknown/stale observations and provenance |

## Invariants

- Keep authoring tokenizer-free. Compilation happens only on export or explicit
  read-only Tick history. Never add tick anchors, sandbox execution or a legacy
  migration layer without new authorization.
- Tokens do not cross event boundaries. Headers/framing inherit source, mask and
  loss. EOS is a label, never a KV key. Same-pass targets cannot see each other;
  closed/replaced content must not return through surviving hidden states.
- Progressive assignment has one implicit Available window. Agent openings are
  deterministic; physical slots can be randomized/reused. Peak capacity excludes
  Available. All-NOOP sleep resumes on external context changes.
- Save structurally broken graph drafts with diagnostics; errors block compiled
  export and warnings do not. Logical windows cannot reopen after EOS.
- Validate JSON booleans separately from integer indices/stages. Distinguish
  missing, null, false, zero and empty inputs. Invalid settings must not reserve
  jobs, discard a draft or mutate a frozen run.
- Use source revision/commit CAS, transactional ownership and worker fences.
  Reject late results after pause, cancel or detach. Pauses preserve resumable
  ownership; detachment is permanent. Manual review/repair requires a detached
  source and one shared operation slot.
- Keep each API request in its endpoint FIFO slot through retries. Exhaustion
  pauses that endpoint for explicit recovery; never silently change models.
- A model/API failure is not a quality verdict. Record per-item review evidence
  and source/checklist identities. Repairs preserve completed spot edits, use
  the matching tier, and end on verdict or context exhaustion. Ready is manual.
- Retain original sampler targets and frozen configuration. Pins remain separate
  from saved weights. Unknown measured metrics stay unavailable, never zero.
- Keep credentials out of project/source/export/log payloads and reports. Validate
  provider output against the authoritative local schema even if decoding uses
  a compatible projection or JSON-object response format.

## Browser and workflow checks

Walk through fresh setup → endpoints/roles → generation → job inspection and
pause/resume/cancel. Also exercise import, authoring, both timeline modes,
review/repair, export and Undo/Redo. Verify empty and locked states.

Delay responses while typing/saving or switching selections. Refresh clean
selected sources as server commits arrive, including unlocking after settlement;
preserve genuinely edited drafts. Check settings forms together so editing a
tool cannot discard endpoint or generation changes. Inspect computed CSS and
screenshots at desktop and compact viewports; DOM assertions alone do not verify
layout or the `hidden` attribute. Check keyboard focus, labels, Escape, menus,
dialogs, overflow and meaningful progress/error messages.

## Validation and completion

Use [TESTING](TESTING.md) for the current commands. Start with the defect's focused
regression, then run relevant shared suites. A typical bounded check is:

```bash
python -m pytest -q tests/test_graph*.py tests/test_ktremix_format.py
npm ci --prefix tests
python tests/run_graph_frontend.py
node tests/project_settings_frontend.mjs
node tests/studio_ui_frontend.mjs
node tests/window_render_frontend.mjs
node tests/run_inputs_frontend.mjs
node tests/provider_options_frontend.mjs
node tests/usage_view_frontend.mjs
node tests/tool_import_frontend.mjs
node tests/show_tools_frontend.mjs
node tests/job_log_levels_frontend.mjs
python tests/run_layout_frontend.py
python tests/run_sampler_frontend.py
python tests/run_generation_frontend.py
python tests/run_dashboard_frontend.py
for f in trajectory_remix/static/*.js; do node --check "$f"; done
ruff check . && ruff format --check .
git diff --check
```

Use installed optional SDK/model dependencies when available and report exact
skips. Do not confuse mocked provider results with live model content quality or
convergence. Full model training and workload harnesses are explicit invocations,
not default CI tests; respect current user authorization and stated budgets.

Review package assets, CLI help, examples and relative links after changing entry
points. Old replay paths have been intentionally retired; new tests should cover
the meaningful graph behavior rather than preserving obsolete APIs. Explain each
removed subsystem and its replacement. Keep [GETTING-STARTED](GETTING-STARTED.md),
[DASHBOARD](DASHBOARD.md), [MCP](MCP.md) and [ACCEPTANCE](ACCEPTANCE.md) current.

Report the concrete changes, reviewed head, checks/results, skips and unresolved
limitations. For multi-pass work, keep a temporary local handoff outside committed
product files, without secrets, including process/session details and next steps.
Verify that `/tmp` artifacts and processes survived before resuming them. Open or
update a focused PR; merge only when the current user has authorized it. A green
suite is evidence of covered behavior, not proof of exhaustive correctness.

Coordinator defaults: `python -m pytest -q tests/test_coordinator_defaults.py` checks the
default checklist keys, that saved checklists are not rewritten, that Draft and Assembly
receive the agent prompt verbatim, and the derived
`strategic_investment` label. Changing a default prompt or checklist changes the checklist
hash and marks existing sign-offs stale, so keep existing keys and update
[PROJECT-SETTINGS](PROJECT-SETTINGS.md) and [GRAPH-GENERATION](GRAPH-GENERATION.md) together.
It shows structure, not live-model quality.

Prompt-scope regressions: `python -m pytest -q tests/test_prompt_scope.py` checks
real stored sampler snapshots stay out of model prompts, review bodies aren't
repeated per frontier, and oversized requests are intercepted before HTTP.

Generation progress: `python -m pytest -q tests/test_generation_progress.py tests/test_graph_jobs.py` verifies first-request visibility, opt-in heartbeats before a
response, stage/event progress, retry reporting and cancellation fencing without
copying model or exception bodies to logs.

Endpoint controls: `python -m pytest -q tests/test_endpoint_sampling.py` checks
wire omission versus explicit zeros, timeout cancellation, accepted ranges,
settings API/schema and frozen run configuration with fake HTTP responses.

Prompt cache order: `python -m pytest -q tests/test_prompt_cache_order.py` checks that
each assembly fix request extends the previous one, that Draft requests in a run share
their system prompt, and that the JSON-mode schema follows the system prompt, so prefix
caches stay effective.

Prompts: `python -m pytest -q tests/test_prompt_settings.py` checks that runs use and freeze
`settings.prompts` (with a version that changes when they're edited), that settings saved
with the retired `method` field still load, that retired `plan` and `flow_yaml` instructions
are refused before reserving attempts, the default-prompts route, and the one-time move of a
customised Flow agent prompt (an appended persona keeps today's default; Load default sticks).

Draft and Assembly: `python -m pytest -q tests/test_draft_assembly.py tests/test_spec_normalize.py tests/test_repair_policy.py`
checks the draft and assemble order, the draft kept in metadata, the builder's tool listing,
chat deliveries and call IDs, corrections for specs that can't be built, the handoff of
errors left after the assembly fix to Fast repair, and that Draft, Assembly and the fix use their
own endpoint roles (with Draft as the fallback). `tests/test_spec_normalize.py` checks
the settled `:echo` slips, tools objects and wrappers, and the builder's fix-it messages. It also validates the worked example in the assemble prompt
against the schema and graph validator. Keep the prompts in `graph/draft_assembly.yaml` and the field
descriptions in `compact_spec.schema` in sync.
Run the full Python suite with `python -m pytest -q tests` before opening a prompt PR.

Phase barriers: `python -m pytest -q tests/test_phase_barrier.py` checks the per-job barriers
(before Assembly, Fast review and Critical review): trajectories park at an enabled barrier
(parked ones don't count toward the active cap), the job pauses when all have arrived and Resume
continues to the next one, the state survives a restart, barriers are validated and frozen with
the run, skipping them lets the run finish, and the phase routes. The old
`generation.phase_barrier` setting is ignored on load.

Held trajectories: `python -m pytest -q tests/test_trajectory_hold.py` checks that invalid review
output or a local error holds only that trajectory while the rest finish, that the job pauses
only when held trajectories alone remain, that Resume retries and clears `needs_attention`, and
that detaching a held trajectory completes the job. Never pause a whole job for one trajectory.

Assembly fix: `python -m pytest -q tests/test_assembly_fix.py` checks that errors and
warnings (not a clean graph) start the fix, that `done` is refused while errors remain, and that
giving up or invalid output hands the graph on to review without pausing.

Repair policy: `python -m pytest -q tests/test_repair_policy.py` checks that a failing repair
verdict keeps the session open, that `give_up` is the failure (with its reason), that `view`
shows the patched source, that context exhaustion starts a fresh round on the current graph,
and that `repair_rounds` caps the rounds.

Hosted providers and usage: `python -m pytest -q tests/test_openrouter_readiness.py
tests/test_openrouter_models.py` checks the bytes-per-token context estimate, extra
request fields and their reserved names, sticky `session_id`, usage kept for cut-off and
non-JSON replies, per-job usage totals (missing values stay missing), the example settings
and the model checker (its cost estimate uses Draft and Assembly token counts from pilots). All HTTP is mocked;
`scripts/openrouter_models.py` is the only part that reads OpenRouter's public model list.
`graph/usage.py` stores counts only, never prompts, responses or credentials.

Output modes: `python -m pytest -q tests/test_output_modes.py` checks that `auto`,
`json_object` and `prompt` endpoints send the right `response_format` (or none) and always
state the schema in the prompt when the server does not enforce it.

Schema corrections: `graph/schema_errors.py` turns a schema violation into a short
correction naming each failing field (it looks inside "object or null" alternatives and
shortens quoted values).

Show tools: `python -m pytest -q tests/test_tool_listing.py` checks the repeatable random
additions, that Assembly writes the listing as a masked event before the opening, and that
manual authoring's listing route matches it (`graph/tool_listing.py`, `graph/tools_api.py`).
