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
| Compiler/export | `native/ktremix`, `graph/compiler.py`, `graph/export.py` | Causal visibility, lifecycle/masks, metadata projection, split families and leased artifacts |
| Generation/review | `graph/generation.py`, `graph/draft_assembly.py`, `graph/authoring_session.py`, `graph/endpoint_prompts.py`, `graph/reviews.py`, `graph/signoffs.py`, `graph/provider.py` | Frozen inputs and prompts, Draft and native Assembly/review sessions, checklist evidence and stage transitions; every review can edit and repairs what fails before signing off |
| Prompts | `graph/draft_assembly.yaml`, `graph/prompt_defaults.py`, `graph/prompt_migration.py`, `graph/prompts_api.py`, `static/prompt-settings.js`, `static/checklist-settings.js` | Packaged defaults, per-project `settings.prompts`, the one-time Flow prompt migration |
| Durable work | `graph/jobs.py`, `graph/bulk.py`, `graph/job_api.py` | Ownership, FIFO, retry slots, fencing, settlement and changed-only Undo |
| REST/MCP | `graph/studio_api.py`, `graph/studio.py`, `graph/agent.py`, `mcp_server.py` | Shared services, loopback authentication, bounded outputs and lifecycle parity |
| Dashboard | `graph.js`, `jobs-view.js`, `metrics-view.js`, `metrics-editor.js`, `complexity-select.js`, `choice-field.js`, `windows-panel.js`, `window-edits.js`, `studio-warnings.js`, `auto-save.js`, `window-render.js`, `markdown-view.js`, `markdown-inline.js`, `window-markdown.css`, `ui-kit.js`, `views.js`, `shell.js`, `layout.js`, `panel-popout.js`, `panel-popout-window.js`, `panel-popout.html`, `panel-popout.css`, `graph.html`, `style.css`, `graph.css`, `project-settings.js`, `sampler.js`, `usage-view.js` within `static/` | Discoverability, real layout, dirty drafts, clean live updates, flicker-free polling, conflicts and accessible controls |
| Comments | `graph/comments.py`, `graph/comments_api.py`, `graph/comment_tasks.py`, `graph/assistant.py`, `graph/assistant_memory.py`, `graph/assistant_metrics.py`, `graph/assistant_proposals.py`, `graph/source_delta.py`, `graph/proposal_review.py`, `graph/proposal_api.py`, `static/comments-view.js`, `static/assistant-proposal.js`, `static/line-diff.js` | Kept outside the source (no revision, review or export effect), writable while locked, targets checked on write and flagged when removed; only a person resolves (agents mark comments addressed); Send to agent freezes open comments, needs the Assistant role, answers through the native `reply` tool, continues the last finished conversation and never changes the stage; its metric tools write only annotation-scope values (origin `assistant_annotation`) and never calculated or export measures. Assistant tools edit a copy-on-write working copy, never the source; the session's edits become a proposal that only a person applies (one Undo-able edit, three-way merged per entity, conflicts refused) or discards (comments reopen, conversation forgotten) |
| Sampler/metrics | `graph/sampling.py`, `graph/task_axes.py`, `graph/metrics.py` | Supplied sampler fidelity, immutable proposals, unknown/stale observations and provenance |

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
  ownership; detachment is permanent. Manual review requires a detached
  source and one shared operation slot.
- Keep each API request in its endpoint FIFO slot through retries. Exhaustion
  pauses that endpoint for explicit recovery; never silently change models.
- A model/API failure is not a quality verdict. Record per-item review evidence
  and source/checklist identities. A review repairs in place at its own tier,
  keeps completed edits, and ends on a signoff (pass or unfeasible) or a limit
  (no verdict). Ready is manual.
- Retain original sampler targets and frozen configuration. Pins remain separate
  from saved weights. Unknown measured metrics stay unavailable, never zero.
- Keep credentials out of project/source/export/log payloads and reports. Validate
  provider output against the authoritative local schema even if decoding uses
  a compatible projection or JSON-object response format.

## Browser and workflow checks

Walk through fresh setup → endpoints/roles → generation → job inspection and
pause/resume/cancel. Also exercise import, authoring, both timeline modes,
review, export and Undo/Redo. Verify empty and locked states.

Delay responses while typing/saving or switching selections. Refresh clean
selected sources as server commits arrive, including unlocking after settlement;
preserve genuinely edited drafts. Check settings forms together so editing a
tool cannot discard endpoint or generation changes. Inspect computed CSS and
screenshots at desktop and compact viewports; DOM assertions alone do not verify
layout or the `hidden` attribute. Check keyboard focus, labels, Escape, menus,
dialogs, overflow and meaningful progress/error messages.

The timeline can be popped out into a second window (`panel-popout.js`), which moves its
elements out of `document`. Look up timeline elements with `byId` (graph.js's `$`), not
`document.getElementById`, or they go missing while the timeline is popped out.

## Validation and completion

The Python test jobs run `./scripts/install.sh` to build the native compiler and install the project
into `.venv`, then `./scripts/test.sh` to test that installation.

Use [TESTING](TESTING.md) for the current commands. Start with the defect's focused
regression, then run relevant shared suites. A typical bounded check is:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps -p ktremix
python -m pytest -q tests/test_graph*.py tests/test_ktremix*.py
npm ci --prefix tests
python tests/run_graph_frontend.py
node tests/project_settings_frontend.mjs
node tests/tokenizer_store_frontend.mjs
node tests/studio_ui_frontend.mjs
node tests/window_render_frontend.mjs
node tests/window_markdown_frontend.mjs
node tests/run_inputs_frontend.mjs
node tests/provider_options_frontend.mjs
node tests/endpoint_collapse_frontend.mjs
node tests/usage_view_frontend.mjs
node tests/metrics_editor_frontend.mjs
node tests/complexity_select_frontend.mjs
node tests/choice_field_frontend.mjs
node tests/argument_form_frontend.mjs
node tests/content_mode_frontend.mjs
node tests/windows_panel_frontend.mjs
node tests/studio_warnings_frontend.mjs
node tests/tool_import_frontend.mjs
node tests/tool_picker_frontend.mjs
node tests/comments_view_frontend.mjs
node tests/comments_auto_send_frontend.mjs
node tests/auto_save_frontend.mjs
node tests/assistant_indicator_frontend.mjs
node tests/assistant_queue_frontend.mjs
node tests/assistant_proposal_frontend.mjs
node tests/conflict_resolver_frontend.mjs
node tests/job_log_levels_frontend.mjs
node tests/job_names_tabs_frontend.mjs
node tests/duplicate_frontend.mjs
node tests/event_rename_frontend.mjs
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
receive the agent prompt verbatim (`tests/test_endpoint_prompts.py` covers every endpoint's
message layout and the upgrade of earlier shipped prompts), and the derived
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

Draft and Assembly: `python -m pytest -q tests/test_draft_assembly.py tests/test_endpoint_prompts.py`
checks the Draft, Assembly and review order, the draft kept in metadata, that a failed Draft is
not reviewed, that Draft and Assembly use their own endpoint roles (with Draft as the fallback),
every endpoint's message layout and the upgrade of earlier shipped prompts. The fake provider
scripts native Assembly calls with the compact spec builder in `tests/compact_spec.py`, a test
fixture only.
Run the full Python suite with `python -m pytest -q tests` before opening a prompt PR.

Phase barriers: `python -m pytest -q tests/test_phase_barrier.py` checks the per-job barriers
(before Assembly, Fast review and Critical review): trajectories park at an enabled barrier
(parked and held attempts free their slots), the job pauses when all have arrived, finished or
been held, and Resume respects the active cap on the way to the next barrier. It also checks
restart, frozen configuration, skipping the barriers, and the phase routes. The old
`generation.phase_barrier` setting is ignored on load.

Held trajectories: `python -m pytest -q tests/test_trajectory_hold.py` checks that invalid review
output or a local error holds only that trajectory while the rest finish, that the job pauses
only when held trajectories alone remain, that Resume retries and clears `needs_attention`, and
that detaching a held trajectory completes the job. Never pause a whole job for one trajectory.

Review sessions: `python -m pytest -q tests/test_review_session.py` checks that a failing
signoff keeps the review open to fix, that an unfeasible signoff records a failed review with
its reason, that views show saved edits, that the exchange and context limits keep edits and the
stage without a verdict, that settings with the retired `repair` options still load, and that a
review queued for the retired pipeline pauses.

Hosted providers and usage: `python -m pytest -q tests/test_openrouter_readiness.py
tests/test_openrouter_models.py` checks the bytes-per-token context estimate, extra
request fields and their reserved names, sticky `session_id`, usage kept for cut-off and
non-JSON replies, per-job usage totals (missing values stay missing), the example settings
and the model checker (its cost estimate uses Draft and Assembly token counts from pilots). All HTTP is mocked;
`scripts/openrouter_models.py` is the only part that reads OpenRouter's public model list.
`graph/usage.py` stores counts only, never prompts, responses or credentials.

Responses API coverage in `tests/test_responses_api.py` exercises the endpoint toggle,
stateless tool replay, incomplete replies, and usage with mocked HTTP.

Plain-text Drafts: `python -m pytest -q tests/test_draft_reply.py tests/test_graph_provider.py`
checks that no request sends `response_format` or a schema contract, that the Draft reply is
read as plain text (a habitual `{"draft": ...}` wrapper is unwrapped, results saved before the
change still load), and that saved `output_mode` settings and the old JSON Draft prompt upgrade.

Schema corrections: `graph/schema_errors.py` turns a schema violation into a short
correction naming each failing field (it looks inside "object or null" alternatives and
shortens quoted values).

Tool schema: `python -m pytest -q tests/test_tool_listing.py` checks the repeatable random
additions, that Assembly writes a `tool_schema` event of the chosen tools before the opening,
and that manual authoring's listing route lists only chosen tools. `tests/test_tool_schema.py`
checks that the compiler adds random extras (`graph/tool_extras.py`) without changing the
stored source, and that chance 0 disables them.
