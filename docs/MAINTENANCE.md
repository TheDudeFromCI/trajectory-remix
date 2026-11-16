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
| Generation/review | `graph/flow.py`, `graph/generation.py`, `graph/reviews.py`, `graph/provider.py` | Frozen inputs, actor context, checklist evidence, stage transitions and single repair sessions |
| Flow editor/walkthrough | `graph/flow_store.py` (project flow in the database; one-time `flow.yaml` import), `graph/flow_document.py`, `graph/walkthrough.py`, `graph/flow_api.py`, `graph/adaptive_driver.py` | Located diagnostics, lossless YAML, revision-checked saves, and walkthrough parity with runs (shared `request_payload`/acceptance; nothing stored) |
| Durable work | `graph/jobs.py`, `graph/bulk.py`, `graph/job_api.py` | Ownership, FIFO, retry slots, fencing, settlement and changed-only Undo |
| REST/MCP | `graph/studio_api.py`, `graph/studio.py`, `graph/agent.py`, `mcp_server.py` | Shared services, loopback authentication, bounded outputs and lifecycle parity |
| Dashboard | `graph.js`, `jobs-view.js`, `metrics-view.js`, `window-render.js`, `flow-panel.js`, `ui-kit.js`, `views.js`, `shell.js`, `graph.html`, `style.css`, `graph.css`, `project-settings.js`, `sampler.js`, `flow-view.js`, `flow-model.js`, `flow-layout.js`, `flow-routes.js`, `flow-canvas.js`, `flow-inspector.js`, `flow-walkthrough.js`, `flow-view.css` within `static/` | Discoverability, real layout, dirty drafts, clean live updates, flicker-free polling, conflicts and accessible controls |
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
  pauses that endpoint for explicit recovery; never silently change models. The Flow
  walkthrough is the one interactive exception: a user-initiated, single, unretried
  Generator call that stores nothing and is never logged.
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
node tests/flow_editor_frontend.mjs
python tests/run_sampler_frontend.py
python tests/run_generation_frontend.py
python tests/run_dashboard_frontend.py
python tests/run_flow_frontend.py
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

Adaptive Flow backend regressions: `python -m pytest -q tests/test_adaptive_flow.py`.
These scripted-provider tests exercise loops, independent tool branches and joins,
causal actor prompts, frozen YAML/resume, typed conditions and bounded failure.
They establish interpreter correctness, not live-model semantic coverage.

Prompt-scope regressions: `python -m pytest -q tests/test_prompt_scope.py` checks
real stored sampler snapshots stay out of model prompts, review bodies aren't
repeated per frontier, and oversized requests are intercepted before HTTP.

Generation progress: `python -m pytest -q tests/test_generation_progress.py tests/test_graph_jobs.py` verifies first-request visibility, heartbeats before a
response, stage/event progress, retry reporting and cancellation fencing without
copying model or exception bodies to logs.

Endpoint controls: `python -m pytest -q tests/test_endpoint_sampling.py` checks
wire omission versus explicit zeros, timeout cancellation, accepted ranges,
settings API/schema and frozen run configuration with fake HTTP responses.

Flow editor and walkthrough: `python -m pytest -q tests/test_flow_document.py
tests/test_flow_walkthrough.py` checks located diagnostics, lossless YAML, revision
conflicts, comment-preserving saves, and that a walkthrough sends the same prompts as a
real run while storing nothing. `node tests/flow_editor_frontend.mjs` covers the editor
draft and walkthrough panel; `python tests/run_flow_frontend.py` drives the Flow tab in
Chromium with a fake Generator.
