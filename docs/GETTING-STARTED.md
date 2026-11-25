# Getting started

This page takes you from a fresh checkout to a working studio, then points to the
next thing you will probably want to do.

## Install

You need Python 3.11+. Node is only needed if you work on the frontend tests; there
is no frontend build step.

Install a stable Rust toolchain to build the native compiler bindings from this checkout.

```bash
./dev.sh
source .venv/bin/activate
```

The script creates or reuses `.venv`, builds the native compiler bindings, and installs the
studio in editable mode with test, MCP, and export dependencies.

## Create a project and open the studio

```bash
python -m trajectory_remix graph-init --dataset project.sqlite
python -m trajectory_remix graph-studio --dataset project.sqlite
```

`graph-init` creates the project database (it refuses to overwrite an existing file).
`graph-studio` serves it at **http://127.0.0.1:8765**. Use `--port` to change the
port. The studio only listens on loopback.

A new project needs **no tokenizer and no model endpoint**. You only need those for
specific features, listed below.

## A first ten minutes

1. **Import an example.** Choose **Project → Import trajectories…** and pick
   [`examples/graph_minimal.json`](../examples/graph_minimal.json). The
   [`graph_tools.json`](../examples/graph_tools.json) example shows a tool call split
   into fragments.
2. **Look around.** The **Dataset** panel on the left lists trajectories, the centre
   shows windows and a timeline, and the **event editor** on the right edits the selected
   event. The tabs at the top switch between the Editor, Jobs, Metrics and Sampler views.
3. **Scrub the timeline.** In the default *Dependency graph* mode, drag the vertical line (or use the step buttons) to move through the
   dependency order. Each step reveals every event that can run in parallel at that level. It is not authored time.
4. **Edit.** Change an event's text, window or dependencies. Add a dependency with the
   picker, or Shift-click a predecessor in the timeline. Changes save as you make them.
5. **Create your own.** Click **+** in the Dataset panel, add windows on the event
   editor's **Windows** tab, then add events and link them under **Runs after** on the **Options** tab. The
   [Dashboard tutorials](DASHBOARD.md#tutorials) walk through this step by step.

Structural errors, such as a missing reference or two unordered writers to one
window, never block saving. They show as diagnostics, keep the draft editable, and
only block compiled export.

## What needs setup

| I want to… | You need | Where |
| --- | --- | --- |
| Author and review sources by hand | Nothing | |
| Generate or run a review | An OpenAI-compatible model endpoint with the draft and reviewer roles | [Project settings](PROJECT-SETTINGS.md#configure-a-model-endpoint) |
| Use **Tick history** preview | A tokenizer profile (opt-in) | [Project settings](PROJECT-SETTINGS.md#tokenizer-profiles) |
| Export compiled KTREMIX | A tokenizer profile | [Export](EXPORT.md) |
| Connect an AI agent | The `mcp` extra and a running studio | [MCP setup](MCP.md) |

Special tokens must already exist in your tokenizer. Remix maps to them, and never
adds or renumbers tokens.

## Where to go next

- [Dashboard](DASHBOARD.md): the everyday workflow, jobs and live updates.
- [Source format](GRAPH-SOURCE.md): what a trajectory contains.
- [Event generation](GRAPH-GENERATION.md): generate and review with your own model.
- [Export](EXPORT.md): produce training data.

Tools and their outputs are authored events; nothing is executed. Projects from the
old replay format are not supported ([details](REPLAY-FORMAT.md)).
