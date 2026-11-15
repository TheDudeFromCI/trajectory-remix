# Trajectory Remix

A local studio for authoring, generating, reviewing and exporting dependency-graph
training trajectories. Python 3.11+, a loopback web UI, and no frontend build step.
Tools and observations are synthetic authored content; Remix never executes them.

```bash
python -m pip install -e '.[mcp,export]'
python -m trajectory_remix graph-init --dataset project.sqlite
python -m trajectory_remix graph-studio --dataset project.sqlite
```

Open `http://127.0.0.1:8765`. Use **Import** to load
[graph_minimal.json](examples/graph_minimal.json). Edit events/windows and their
predecessors, inspect diagnostics, and scrub the dependency timeline. Saving
structural errors keeps them editable; they block compilation. Revisions and
locks protect against overwriting another editor or an attached pipeline.

Graph mode, generation, reviews and metrics need no tokenizer. For opt-in **Tick
history**, first define a compatible tokenizer profile in Project Settings, then
select it in client Preferences. The default refresh is a 500 ms trailing debounce.
Compiled export selects its own profile. Profiles never add or renumber tokens.

**Sampler Matrix uses the selected Workbench B UI.** Tune weights, copy/remove
entries, add/remove rules, inspect constraints and linked metrics, then save the
project draft. Pins affect proposal/generation requests independently of weights.
Runs freeze inputs before admission. Unknown actual metrics remain unavailable;
**Calculate Missing Metrics** is an explicit action.

Configure your own endpoints and roles in Project Settings to generate or run
Fast/Critical Review & Repair. These actions call the configured models. Generated
sources are not automatically Ready; Ready is a manual stage. Independent jobs
continue when dialogs close, with explicit pause/resume/cancel and per-job logs.
Restart and exhausted endpoints require manual Resume.

Export source JSONL or compiled KTREMIX through **Export**. Ready is the default
minimum stage; stale reviews are included by default. Families stay together in
separate split shards. Downloads live in `/tmp`, expire after 24 hours and can
also disappear through operating-system cleanup. Clearing logs does not clear
sources, checkpoints, evaluations or ownership.

For dashboard setup and job controls, see [Dashboard](docs/DASHBOARD.md) and
[Project settings](docs/PROJECT-SETTINGS.md).

| Task | Guide |
| --- | --- |
| Author source and synthetic tools | [Source format](docs/GRAPH-SOURCE.md), [Studio](docs/GRAPH-STUDIO.md), [Tools/chat](docs/TOOLS.md) |
| Tune matrix and metrics | [Workbench B](docs/SAMPLER-WORKBENCH.md), [Metrics](docs/GRAPH-SAMPLER-METRICS.md) |
| Generate/review/repair | [Event Flow](docs/GRAPH-GENERATION.md) |
| Compile and export | [Compiler/export](docs/GRAPH-COMPILER.md), [KTREMIX](docs/KTREMIX.md) |
| Connect an agent | [MCP setup/examples](docs/MCP.md), [bundled Skill](trajectory_remix/Skill.md) |
| Inspect jobs and recovery | [Jobs](docs/GRAPH-JOBS.md) |
| Validate the release | [Small checks](docs/TESTING.md), [user-machine acceptance](docs/ACCEPTANCE.md) |

Old replay/tick-anchor projects and executable sandbox formats are unsupported.
There is no compatibility reader or migration. Startup and imports reject them;
retain old datasets separately if you need historical source material.

User-machine 25k-stored/20-active, local-model quality and full training acceptance
remain pending until actual results are supplied. Historical replay demo timings
are not evidence about this graph implementation. No performance or quality
claim follows from the deterministic checks.
