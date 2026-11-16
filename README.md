# Trajectory Remix

Trajectory Remix is a local studio for authoring, generating, reviewing and
exporting **dependency-graph training trajectories**: records of what an agent
sees, does and says across several windows at once, used to train Project Kidra
models.

With it you can:

- **Author** trajectories by hand in a browser studio, or through an AI agent over
  MCP.
- **Generate** new ones from a tunable sampler, using your own model endpoints, then
  have a fast and a critical reviewer check and repair them.
- **Track** every job, review and stage, and recover cleanly from pauses and restarts.
- **Export** approved trajectories as raw JSONL, or compiled into compressed
  **KTREMIX** shards for training.

It runs entirely on your machine: Python 3.11+, a web UI on `127.0.0.1`, and no
frontend build step. Tools and their results in a trajectory are **authored
content**. Remix never executes them.

## Quick start

```bash
python -m pip install -e '.[mcp,export]'
python -m trajectory_remix graph-init --dataset project.sqlite
python -m trajectory_remix graph-studio --dataset project.sqlite
```

Open **http://127.0.0.1:8765**, choose **Project → Import trajectories…**, and load
[`examples/graph_minimal.json`](examples/graph_minimal.json). Select an event to edit
its window and dependencies, and scrub the timeline to see the trajectory build up.
New projects need no tokenizer.

Next steps are in [Getting started](docs/GETTING-STARTED.md).

## How it fits together

1. **Source.** A trajectory is a graph of *events* (agent text, external text, tool
   calls and responses, chat messages) living in *windows*. Events say which earlier
   events they depend on, rather than being placed on a clock. See
   [Source format](docs/GRAPH-SOURCE.md).
2. **Studio.** Edit sources, run jobs and review work in the browser
   ([Dashboard](docs/DASHBOARD.md), [Studio reference](docs/GRAPH-STUDIO.md)).
3. **Generate and review.** The sampler proposes a scenario, your generator model
   writes the events one at a time, and optional reviewers sign off or repair
   ([Event generation](docs/GRAPH-GENERATION.md)).
4. **Stage.** Each trajectory has a stage from Unfinished (-3) up to **Ready (3)**.
   Ready is always a manual decision.
5. **Export.** Ready trajectories are written as source JSONL or compiled KTREMIX
   shards with a tokenizer profile ([Export](docs/EXPORT.md)).

## Documentation

| I want to… | Read |
| --- | --- |
| Install and make a first project | [Getting started](docs/GETTING-STARTED.md) |
| Use the studio day to day | [Dashboard](docs/DASHBOARD.md), [Studio reference](docs/GRAPH-STUDIO.md) |
| Connect models and tokenizers | [Project settings](docs/PROJECT-SETTINGS.md) |
| Understand the source format | [Source format](docs/GRAPH-SOURCE.md), [Synthetic tools and chat](docs/TOOLS.md) |
| Tune what gets generated | [Sampler Matrix](docs/SAMPLER-WORKBENCH.md), [Metrics](docs/METRICS.md) |
| Generate, review and repair | [Event generation](docs/GRAPH-GENERATION.md) |
| Edit the generation flow or debug it step by step | [Flow view](docs/DASHBOARD.md#the-flow-view), [Walkthrough](docs/DASHBOARD.md#walk-through-a-generation-step-by-step) |
| Watch, pause and recover jobs | [Jobs](docs/GRAPH-JOBS.md) |
| Export for training | [Export](docs/EXPORT.md), [Compiler](docs/GRAPH-COMPILER.md), [KTREMIX format](docs/KTREMIX.md) |
| Try training on exported data | [Training demo](docs/KTREMIX-TRAINING-DEMO.md) |
| Let an AI agent drive Remix | [MCP setup](docs/MCP.md), [Authoring with an agent](docs/MCP-AUTHORING.md) |
| Check a local model | [Local-model validation](docs/GRAPH-LOCAL-LLM.md) |
| Know where data lives | [Storage](docs/STORAGE.md) |
| Run or review the checks | [Testing](docs/TESTING.md), [Acceptance](docs/ACCEPTANCE.md), [Maintenance](docs/MAINTENANCE.md) |

## Good to know

- **Old projects are not supported.** The previous replay/tick-anchor format and the
  executable sandbox are gone, with no reader or migration. Imports reject them, so
  keep old datasets separately if you need the history. See
  [From the old replay format](docs/REPLAY-FORMAT.md).
- **Model calls only happen when you ask.** Generation and review use the endpoints
  you configure. Credentials come from environment variables and are never saved in
  projects.
- **Verification status.** The deterministic tests pass, and the demo training run
  met its gates on one CPU run ([results](docs/VALIDATION-RESULTS.md)). Full-scale
  (25k stored / 20 active) and local-model quality acceptance are still pending, and
  nothing here is a performance or quality guarantee.
