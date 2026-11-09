# Maintenance sweeps for agents

Use this guide for a general codebase review and repair pass. Prioritize data
loss, incorrect replay/training behavior, broken workflows, and misleading
documentation. A sweep should produce concrete fixes and evidence, with an
honest account of what was reviewed and what remains unverified.

This is a reusable checklist, not a record that these checks have already passed.
Confirm current behavior from code and tests; update this guide when contracts or
entry points change. See [README](../README.md) for product behavior and
[TESTING](TESTING.md) for detailed coverage.

## Start and scope the pass

- [ ] Read applicable `AGENTS.md` files, the user's request, and any previous
  handoff log. Check the current branch, working tree, recent commits, and PR
  state before editing. Preserve unrelated work.
- [ ] Identify the runtime, available dependencies, and existing test commands.
  Reuse the project's environment. Record missing optional dependencies and
  distinguish environment failures from application regressions.
- [ ] Review recent changes and the paths they affect, including docs, examples,
  packaged assets, and both dashboard and MCP callers of shared services.
- [ ] Establish a baseline for the affected area. Reproduce suspected defects
  before fixing them where practical; preserve baseline failure details.
- [ ] Keep fixes reviewable. Separate confirmed defects from possible refactors;
  avoid speculative rewrites and unrelated dependency upgrades.
- [ ] Use temporary datasets and deterministic model fakes. Do not point tests at
  a user's working database or invoke configured paid model endpoints as an
  incidental smoke check.

## Review map and areas needing special attention

Paths in this table are relative to `trajectory_remix/`; tests live in `tests/`.

| Area | Main entry points | What to inspect |
| --- | --- | --- |
| Replay and training | `compiler.py`, `training.py`, `preview.py` | Causality, token boundaries, window lifecycle, losses, bounded previews, chunk reconstruction. |
| Persistence and edits | `storage.py`, `dataset.py`, `changes.py`, `authoring.py` | Atomic writes, revision conflicts, UUID identity, undo/redo, lazy reads, import/export integrity. |
| Generation and recovery | `scenario_runner.py`, `model_session.py`, `content_first.py`, `generation_*.py`, `scenarios.py` | Task ownership, retries, cancellation, checkpoint reuse, logs, concurrency and provider compatibility. |
| Review and approval | `validation.py`, `review.py`, `trajectory_review.py`, `guidance.py` | Source-bound assessments, complete checklists, repair conflicts, explicit state transitions. |
| Simulated tools | `tools/`, `sandbox_replay.py`, `sandbox_cache.py`, `tool_sessions.py` | Permissions, timeouts, cancellation, state isolation, historical visibility and schema compatibility. |
| API and MCP | `server.py`, `mcp_server.py`, `authoring.py` | Input validation, consistent transport behavior, local-service guards, bounded output, background-job lifetime. |
| Browser UI | `static/app.js`, `static/index.html`, `static/style.css` | Async edit races, selection/search state, usable layouts, accessible controls and actual CSS visibility. |
| Search and metrics | `search.py`, `metrics.py`, `metric_jobs.py`, `metric_annotations.py`, `metadata.py` | SQL/draft parity, pagination, population definitions, cache invalidation, missingness and provenance. |
| Delivery and reference material | `__main__.py`, `../pyproject.toml`, `../scripts/`, `../docs/`, `guides/`, template directories | CLI behavior, installation, package data, executable examples, stale feature descriptions. |

### Replay, tokenization, and exports

- [ ] Preserve external-first visibility: same-tick generated targets cannot
  attend to each other or themselves. Compare with `visible_to`, not a generic
  flattened chat sequence.
- [ ] Check BOS/header insertion, generated EOS closure, NOOP supervision versus
  durable memory, and context eviction after closure. Loss exclusion must not
  remove conditioning context.
- [ ] Exercise simultaneous windows, pending/closed windows, content replacement,
  anchor dependencies, segmented losses, and declared slot limits. Tokenization
  boundaries and custom special-token spellings must remain consistent.
- [ ] Keep `{N_TICK:...}` and `{WINDOW:...}` authoring syntax separate from literal
  tool arguments and observations. Avoid silently modifying the tokenizer.
- [ ] Check that tool-schema listings precede visible requests while independent
  reasoning and calls remain concurrent; inspect diagnostics on generated flows.
- [ ] Preserve Ready-gated training export, ordered chunks and required prefixes,
  row accounting, masks, lifecycle/slot metadata, and atomic output replacement.
- [ ] Treat valid compilation and preview availability separately. Exercise large
  frames, bounded range reads, cache expiry/recovery, and seek/playback without
  downloading expanded replay prefixes into the browser.

See [EXPORT](EXPORT.md), [SANDBOX](SANDBOX.md), and [TOOLS](TOOLS.md).

### Storage, APIs, and background work

- [ ] Reject invalid types and boundary values with useful diagnostics. Include
  missing versus explicit null/false/zero/empty values; JSON booleans are not
  valid integer indices merely because Python treats `bool` as an `int` subtype.
- [ ] Check stale revisions, concurrent clients, edits during background jobs,
  transactional rollback, immutable UUIDs, duplicate imports, and undo/redo.
- [ ] Keep startup, listing, polling, and single edits bounded for large datasets.
  Check filtering before pagination and avoid loading all replay sources.
- [ ] Test pause, cancel, resume, restart, client disconnects, duplicate job starts,
  and failures between inference completion and checkpoint persistence. Completed
  work and expensive proposals should remain inspectable and reusable.
- [ ] Preserve the separation between creation, semantic review, and human Ready
  approval. Successful creation saves Pending drafts; semantic review cannot
  silently grant Ready. Check source/checklist fingerprints when recording or
  reusing assessments.
- [ ] Keep provider keys out of saved datasets, exports, logs, and API responses.
  Verify local API tokens, origin/Host restrictions, MCP session handling, and
  request/output limits when changing transport code.
- [ ] Validate structured output against its original schema even when the
  provider's decoding schema is projected for compatibility. Check retry limits,
  retained invalid responses, optional inference-tokenizer behavior, and clear
  reasons for skipped local budget checks.
- [ ] Check cache keys against relevant source, tokenizer, tool, and definition
  changes. Metadata-only edits should reuse caches where supported; content edits
  must not reuse stale results.
- [ ] Keep sandbox state isolated across samples and sessions. Verify that future
  tool outcomes cannot appear while scrubbing earlier ticks, and cancelled work
  cannot publish later results. Built-in tools simulate work; custom Python tools
  are trusted server-side modules with a different execution boundary.
- [ ] Keep unknown metric labels/provenance unknown, preserve nullable
  denominators, and compare all/Ready/train/original populations explicitly.

See [MCP](MCP.md), [SCENARIOS](SCENARIOS.md), [METRICS](METRICS.md), and the
[search guide](../trajectory_remix/guides/search.md).

### UI, UX, and accessibility

- [ ] Walk through create/import, select/search/page, edit, compile, replay,
  metadata, undo/redo, generation/recovery, review, and source export as relevant.
  Verify empty datasets, malformed drafts, errors, loading and busy states.
- [ ] Deliberately delay responses: type during saving/compilation, switch samples
  rapidly, change search while a request is pending, and shrink a paged result
  set. Newer edits and selections must take precedence over stale responses.
- [ ] Ensure actions needing saved state wait for the save; inspect snapshots
  captured across `await` boundaries, not just final server responses. Verify
  external MCP edits and local pending drafts do not silently overwrite each other.
- [ ] Inspect both DOM state and computed CSS in a real browser. A `hidden`
  attribute can be overridden by a more specific display rule. Check clipping,
  overflow, text wrapping, editor height, menus, dialogs and resize handles at
  desktop and compact sizes (for example, 1440×1000 and 1024×768).
- [ ] Check keyboard navigation, visible focus, tab/panel semantics, accessible
  names, dialog focus and Escape behavior, shortcut handling inside text inputs,
  status/error announcements, and disabled/busy controls.
- [ ] Make progress, validation states, recovery choices and error messages clear.
  Keep unavailable actions discoverable or explain why they cannot run. Check
  that error handling preserves the user's draft and offers a useful next step.

### Legacy code, docs, dependencies, and refactoring

- [ ] Find unreachable dialogs, orphaned selectors/handlers, obsolete routes,
  stale comments, dead imports, and duplicated logic. Confirm callers and
  compatibility obligations before removing them.
- [ ] Distinguish retired functionality from intentional import aliases, old
  database support, saved review contracts, and fixtures testing legacy behavior.
  Do not remove compatibility merely because its name contains `legacy`.
- [ ] Check README, reference docs, short agent guides, template prompts, CLI help,
  and example files against current UI labels, commands, defaults and limits.
  Keep source-JSON export distinct from training-token export.
- [ ] Check dependency support and lockfile consistency when relevant. Investigate
  concrete incompatibilities; dependency age alone does not establish a bug.
- [ ] Review large modules and repeated dashboard/MCP logic for useful extraction.
  Establish behavior-preserving boundaries and focused tests before splitting
  compiler, generation, or persistence code.
- [ ] If changing assets, templates, entry points or dependencies, verify package
  data in `pyproject.toml` and exercise installation outside the source checkout.

## Validation workflow

Run commands from the repository root. `bash scripts/install.sh` creates `.venv`
and installs the application with test and MCP extras; use an existing working
environment when available. Python 3.11+ is required; DOM checks need Node 22+.

```bash
# Run a focused regression first; choose the files relevant to the change.
.venv/bin/python -m pytest -q tests/test_api.py

# Run the complete Python suite for a broad sweep or shared behavioral changes.
.venv/bin/python -m pytest -q

# Run dashboard integration checks when UI or its API contracts change.
npm ci --prefix tests
.venv/bin/python tests/run_frontend.py
.venv/bin/python tests/run_frontend.py editor-state.test.mjs

# Check modified JavaScript and patch whitespace.
node --check trajectory_remix/static/app.js
git diff --check
```

DOM checks use a temporary backend and an emulated browser; they do not verify
visual layout. Use the available browser tooling for real-browser checks, inspect
screenshots, and record the actual viewports/workflows exercised. See
[TESTING](TESTING.md) to select additional compiler, storage, scenario, tool,
official-client MCP, migration, and packaging regressions.

Local HTTP/MCP/DOM checks require loopback socket access. In a restricted
execution environment, use its supported permission mechanism when sockets are
blocked. Diagnose a binding/connectivity failure before changing application
logic. Report exact skips and blockers; missing optional SDKs or model fakes do
not establish live-provider compatibility. Do not put credentials in the report.

For each meaningful fix, add or adapt a regression that would catch the original
failure. Wait for observable state or controlled response barriers in async
tests. Once relevant checks pass, broaden or repeat them only for new changes,
failures, or unresolved concerns. Documentation-only follow-ups usually need
command/link consistency and diff checks, not another full runtime test run.

## Handoff and completion checklist

- [ ] Re-review the diff for unintended behavior changes, generated artifacts,
  credentials, user data, and unrelated edits.
- [ ] Record the reviewed areas, fixed defects and their practical impact,
  validation commands/results, skips, and any remaining risks. A passing suite
  is evidence for its coverage, not proof of an exhaustive codebase audit.
- [ ] Separate confirmed unresolved defects from optional improvements. Large
  compiler/generation refactors and broader keyboard-accessibility reviews are
  reasonable follow-up areas when the pass does not cover them.
- [ ] For work spanning passes, keep a temporary handoff log outside committed
  product files. Include base/head commits, branch/PR, scope covered, reproduction
  steps, completed changes, test results/log locations, pending process/session
  details, blockers, and concrete next actions. Never include secrets. Do not
  assume a prior agent's `/tmp` files or processes survived; verify them.
- [ ] Commit the scoped changes and submit or update the requested PR. Explain
  the problem, resulting behavior, rationale, validation and material limitations.
  Check existing PR state before choosing a branch; do not merge as part of a
  maintenance sweep unless requested.
- [ ] Update the handoff with the final commit and PR URL and state whether work
  remains. Keep this reusable guide and its links current when the workflow changes.
