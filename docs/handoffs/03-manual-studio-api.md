# 03 — Retained studio UI, REST surface and first usable manual milestone

Read [CONTEXT](CONTEXT.md), contracts **§3–4 and §9–10**. Requires 01/02 and
05's job/resource primitives. Suggested branch: `feat/graph-studio-rest`.

## Inspect first

`static/index.html`, `static/app.js`, `static/style.css`, `server.py`, MCP command
handlers/`mcp_server.py`, project settings/metrics routes and existing DOM tests.
Preserve the overall UI rather than inventing a new workspace. Do not retain
sandbox/tick-marker controls for compatibility.

## Scope

- Dashboard project/job operations go entirely through REST and the shared
  application service. Preferences and delta Undo remain browser-local.
- Retain dataset/window/editor panes, forms and advanced Source JSON. Structured
  chat handler supplies MSG; tool forms/raw JSON preserve partial arguments.
- Automatic token blocks use bordered uppercase labels. Tool/response begin/end
  and Close window are checkboxes; no token-insertion preference/placeholders.
- Timeline default Graph mode and optional Tick history using current renderer.
  Preview profile selected in Preferences, definitions in Project Settings,
  independent Export selection. Scrub both; don't add a duplicate before/after UI.
- Tick history refresh is **500 ms trailing debounce**, configurable; reset on
  rapid edits, cache, supersede stale requests/results. Graph mode never tokenizes.
  Hover dependencies replace marker indicators. Picker/direct linking in both
  modes; editing changes source edges/IDs, never preview tick coordinates.
- REST import/export background jobs, artifact references and error reporting.
  Project creation with no tokenizer; reject incompatible profiles without adding
  tokens. No full dataset payload on every refresh.
- Independent job dialogs/logs, bottom status bar and pause/cancel/resume controls.
  Preserve editor drafts/selection; no global single-current-job client state.
- Project Settings and Preferences remain separate. Log cap/clear/export and
  missing-split warnings integrate the existing panels.
- Essential agent-readable MCP tasks and focused Skills/guides for manual graph,
  dependencies/windows, tool/chat authoring and export. Do not force complete
  REST parity or dump the full manual/source on each agent call.

First usable milestone must support manual authoring → diagnostics → timeline →
raw/compiled export with the actual compiler. Do not present old generation as
working with the new model before 06 is implemented.

## Required small checks

Use tiny sources/fake clocks and DOM/API integration tests: dependency editing,
both render modes/scrub, trailing debounce and stale response rejection, raw/form
switch preserving content, JSON errors, source/metadata separation, missing split
versus intentional Auto, multiple job inspection and stable selection, source
locks/CAS, REST-only project operations and finalized artifact downloads.

No 25k fixtures, UI throughput measurements, live model calls or full training.
Do not add a second metrics panel. The three matrix editor prototypes are separate
handoffs and must share the 04 backend. API interface/schema/guide additions need
public docs; keep the temporary planning packet out of the implementation PR diff.

Deliver a focused draft PR with screenshots/functional evidence where available,
specific limitations and small checks. Do not merge.
