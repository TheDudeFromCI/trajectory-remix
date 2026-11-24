# Bounded correctness checks

These tests use small deterministic graph/codec fixtures, fake providers and
clocks. No scale workloads, throughput measurements or live models run here.

```bash
python -m pip install ./native/ktremix-python -e '.[test,mcp,export]'
python -m pytest -q tests/test_graph*.py tests/test_ktremix_format.py
npm ci --prefix tests
python tests/run_graph_frontend.py
node tests/project_settings_frontend.mjs
node tests/studio_ui_frontend.mjs
node tests/window_render_frontend.mjs
node tests/tool_picker_frontend.mjs
node tests/comments_view_frontend.mjs
node tests/comments_auto_send_frontend.mjs
node tests/run_inputs_frontend.mjs
node tests/provider_options_frontend.mjs
node tests/endpoint_collapse_frontend.mjs
node tests/usage_view_frontend.mjs
node tests/tool_import_frontend.mjs
node tests/job_log_levels_frontend.mjs
node tests/job_names_tabs_frontend.mjs
node tests/duplicate_frontend.mjs
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

Graph DOM tests use happy-dom. `studio_ui_frontend.mjs` checks that polling
re-renders (job list, job detail, progress bars, keyed rows) cause no DOM mutations
when nothing changed, which is what keeps the dashboard from flickering. `window_render_frontend.mjs`
covers the readable window view (NOOP folding, chat bubbles, JSON formatting).
`project_settings_frontend.mjs` also covers the **Generation prompts** section. `tool_picker_frontend.mjs` covers the tool checkboxes that replace the text box for `tool_schema` events: chosen versus random tools, relisting, hidden extras, tools missing from the project, failed requests and the locked state. `comments_view_frontend.mjs` covers the reviewer comments panel: choosing a target, composing from a window card, adding, resolve/reopen, accept/request changes on an agent's fix, replies, removed targets and polling without DOM changes; `python -m pytest -q tests/test_comments.py` covers the REST routes, replies, the comment filters, the open → addressed → resolved lifecycle (agents can't resolve), upgrading older comment tables and MCP tasks, and that comments never touch the source revision. `python -m pytest -q tests/test_assistant.py` drives Send to agent with a scripted fake model: spot edits, addressing and replying, frozen comments, incomplete finishes, the context budget and the missing-role error. `job_log_levels_frontend.mjs` covers
the coloured job log lines. `run_layout_frontend.py` drives a real browser through the splitters, the collapsible trajectory bar
and the timeline controls that move to the status bar, and checks real layout rather than DOM state. Layout, sampler, dashboard and
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
responses, Workbench B structure/pins, and fake-provider generation/reviews/repair.
The old phase visibility predicate remains only as a test oracle. Native schema
projection regressions remain independently of the retired replay runtime.

The old replay/sandbox tests and fixtures were removed with that implementation;
they are not acceptance coverage for the graph path. See [acceptance](ACCEPTANCE.md)
for user-machine gates and the integration record. Manual workload scripts live
outside test discovery, require `--user-machine`, and refuse CI. Imports and
`--help` do not start endpoints, seed data or download weights.
