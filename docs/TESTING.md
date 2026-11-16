# Bounded correctness checks

These tests use small deterministic graph/codec fixtures, fake providers and
clocks. No scale workloads, throughput measurements or live models run here.

```bash
python -m pip install -e '.[test,mcp,export]'
python -m pytest -q tests/test_graph*.py tests/test_ktremix_format.py
npm ci --prefix tests
python tests/run_graph_frontend.py
node tests/project_settings_frontend.mjs
node tests/studio_ui_frontend.mjs
node tests/window_render_frontend.mjs
node tests/run_inputs_frontend.mjs
python tests/run_sampler_frontend.py
python tests/run_generation_frontend.py
```

Graph DOM tests use happy-dom. `studio_ui_frontend.mjs` checks that polling
re-renders (job list, job detail, progress bars, keyed rows) cause no DOM mutations
when nothing changed, which is what keeps the dashboard from flickering. `window_render_frontend.mjs`
covers the readable window view (NOOP folding, chat bubbles, JSON formatting) and checks
that the Flow YAML writer folds long text losslessly. Sampler and
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
log/artifact lifecycle, REST/MCP, both timeline modes, trailing debounce and stale
responses, Workbench B structure/pins, and fake-provider generation/reviews/repair.
The old phase visibility predicate remains only as a test oracle. Native schema
projection regressions remain independently of the retired replay runtime.

The old replay/sandbox tests and fixtures were removed with that implementation;
they are not acceptance coverage for the graph path. See [acceptance](ACCEPTANCE.md)
for user-machine gates and the integration record. Manual workload scripts live
outside test discovery, require `--user-machine`, and refuse CI. Imports and
`--help` do not start endpoints, seed data or download weights.
