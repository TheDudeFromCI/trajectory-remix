# Bounded correctness checks

These tests use small deterministic graph/codec fixtures, fake providers and
clocks. No scale workloads, throughput measurements or live models run here.

```bash
python -m pip install ./native/ktremix-python -e '.[test,mcp,export]'
python -m pytest -q tests/test_graph*.py tests/test_ktremix_format.py
npm ci --prefix tests
python tests/run_graph_frontend.py
node tests/project_settings_frontend.mjs
node tests/tokenizer_store_frontend.mjs
node tests/studio_ui_frontend.mjs
node tests/window_render_frontend.mjs
node tests/window_markdown_frontend.mjs
node tests/tool_picker_frontend.mjs
node tests/comments_view_frontend.mjs
node tests/comments_auto_send_frontend.mjs
node tests/auto_save_frontend.mjs
node tests/assistant_indicator_frontend.mjs
node tests/assistant_queue_frontend.mjs
node tests/assistant_proposal_frontend.mjs
node tests/conflict_resolver_frontend.mjs
node tests/run_inputs_frontend.mjs
node tests/provider_options_frontend.mjs
node tests/endpoint_collapse_frontend.mjs
node tests/usage_view_frontend.mjs
node tests/metrics_editor_frontend.mjs
node tests/draft_tab_frontend.mjs
node tests/complexity_select_frontend.mjs
node tests/choice_field_frontend.mjs
node tests/argument_form_frontend.mjs
node tests/content_mode_frontend.mjs
node tests/windows_panel_frontend.mjs
node tests/studio_warnings_frontend.mjs
node tests/tool_import_frontend.mjs
node tests/job_log_levels_frontend.mjs
node tests/job_names_tabs_frontend.mjs
node tests/duplicate_frontend.mjs
node tests/event_rename_frontend.mjs
python tests/run_layout_frontend.py
python tests/run_sampler_frontend.py
python tests/run_generation_frontend.py
```

Log status levels and failure explanations (`python -m pytest -q tests/test_log_status.py`)
check that each log line keeps its level, that old log tables gain the column, that HTTP, timeout,
connection and missing-key failures say why without copying provider bodies or exception text, and
that heartbeat lines stay off by default.

Coordinator-first defaults (default checklist keys, saved checklists left alone, the agent
prompt delivered verbatim to Draft and Assembly, the derived
`strategic_investment` label and the reset endpoint) are covered by
`python -m pytest -q tests/test_coordinator_defaults.py`. The reset button's confirmation is
covered in `project_settings_frontend.mjs`. These use fake providers, so they check
structure, not prompt quality.

`python -m pytest -q tests/test_json_encoding.py` checks that API responses keep dict keys starting with `_sa`,
which FastAPI drops by default.

`python -m pytest -q tests/test_settings_storage.py` checks the settings tables: one stored copy per
tokenizer, snapshots pruned except those used by recent saves, trajectories and jobs, stale saves merging inside
that window and conflicting after it, and the in-place upgrade of projects in the earlier layout.

`tokenizer_store_frontend.mjs` checks that imported tokenizer definitions stay out of the
settings JSON draft as short placeholders, are restored unchanged for saving and still fill
the control dropdowns; `python -m pytest -q tests/test_request_errors.py` checks that a
rejected settings save does not echo the submitted tokenizer back.

Graph DOM tests use happy-dom. `studio_ui_frontend.mjs` checks that polling
re-renders (job list, job detail, progress bars, keyed rows) cause no DOM mutations
when nothing changed, which is what keeps the dashboard from flickering. `window_render_frontend.mjs`
covers the readable window view (NOOP folding, chat bubbles, JSON formatting). `window_markdown_frontend.mjs` covers its Markdown rendering: inline and block syntax, escaped HTML and unsafe links, plain text passing through unchanged, unterminated (streaming) syntax and Raw mode.
`project_settings_frontend.mjs` also covers the **Generation prompts** section. `tool_picker_frontend.mjs` covers the tool checkboxes that replace the text box for `tool_schema` events: chosen tools, relisting, tools missing from the project, failed requests and the locked state. `comments_view_frontend.mjs` covers the reviewer comments panel: choosing a target, composing from a window card, adding, resolve/reopen, accept/request changes on an agent's fix, replies, removed targets and polling without DOM changes; `python -m pytest -q tests/test_comments.py` covers the REST routes, replies, the comment filters, the open → addressed → resolved lifecycle (agents can't resolve), upgrading older comment tables and MCP tasks, and that comments never touch the source revision. `python -m pytest -q tests/test_assistant.py` drives Send to agent with a scripted fake model over native tool calls: edits, the `reply` tool, reminders until every comment has one reply, frozen comments and prompts, continuing the kept conversation on the next send, the exchange limit, the context budget and the missing-role error. `python -m pytest -q tests/test_assistant_proposals.py` covers proposed changes: edits wait in a copy and leave the trajectory untouched, a session without edits proposes nothing, Apply merges onto unrelated newer edits, resolves the addressed comments and can be undone, a conflicting edit blocks Apply and the next send, Discard reopens the comments and forgets the conversation, a follow-up send continues from the proposal rebased onto the person's edits, and the REST routes; `python -m pytest -q tests/test_source_delta.py` covers the entity-level diff and three-way merge behind it, and `assistant_proposal_frontend.mjs` the line diff and the proposal panel (Apply, Discard, Request changes, conflicts, the lock and quiet polling). Save conflicts: `python -m pytest -q tests/test_save_rebase.py tests/test_conflict_resolution.py` covers merging stale saves, the listed conflict values and saving with Keep mine / Keep theirs; `conflict_resolver_frontend.mjs` covers the resolve dialog (labels, the saved-vs-mine diff, choices, cancel), and `graph_frontend.mjs` the clickable **Failed to Save** marker end to end. `python -m pytest -q tests/test_assistant_metrics.py` covers the Assistant's `view_metrics` and `set_metric` tools: values proposed with the session's edits, staleness after a later edit, clearing with `null`, and rejecting invalid values and read-only (calculated or export) metrics. `python -m pytest -q tests/test_endpoint_prompts.py` covers the message layout of every endpoint, the comment table and the upgrade of earlier shipped prompts. `job_log_levels_frontend.mjs` covers
the coloured job log lines. `run_layout_frontend.py` drives a real browser through the splitters, the fixed trajectory bar and its fields in the Metrics tab
and the timeline controls that move to the status bar, and checks real layout rather than DOM state. It also runs `panel_popout_frontend.mjs`, which pops the timeline out into a second window, drives the editor from there (selecting events, stepping) and docks it back. It also reloads the popup and the editor (both reconnect), checks that two editors keep separate popups, and that closing an editor closes its popup. It also runs `edit_focus_frontend.mjs`, which types into a tool argument field across auto-saves and polls and checks that the field keeps focus and every keystroke lands. Layout, sampler, dashboard and
generation browser checks use optional Node `playwright` and Chromium (`CHROMIUM_PATH`, default `/usr/bin/chromium`).
Browser screenshots are opt-in with `UPDATE_SCREENSHOTS=1`; ordinary verification
does not rewrite documentation images. No frontend runtime packages are required.

Install CPU PyTorch separately to run the real compiler → shard → sampler → model
connectivity checks:

```bash
python -m pytest -q tests/test_ktremix_training.py
```

This file contains exactly one optimizer update, with tiny datasets. Do not rerun
that case in a session limited to one update. The remaining cases check causal
invariance, compiler/runtime parity and framing without training. Missing torch
skips this module; do not report training connectivity verified in that case.

Coverage includes strict source grammar/diagnostics, metadata/profile independence,
framing/EOS/NOOP/replacement, causal frontiers, split families, CRC/bounds, source
CAS, locks/fencing, frozen runs, endpoint FIFO/retries/manual recovery, bulk deltas,
log/artifact lifecycle, REST/MCP (legacy and 2026-07-28 protocols), both timeline modes, trailing debounce and stale
responses, Workbench B structure/pins, and fake-provider generation and reviews.
The old phase visibility predicate remains only as a test oracle. Native schema
projection regressions remain independently of the retired replay runtime.

The old replay/sandbox tests and fixtures were removed with that implementation;
they are not acceptance coverage for the graph path. See [acceptance](ACCEPTANCE.md)
for user-machine gates and the integration record. Manual workload scripts live
outside test discovery, require `--user-machine`, and refuse CI. Imports and
`--help` do not start endpoints, seed data or download weights.
