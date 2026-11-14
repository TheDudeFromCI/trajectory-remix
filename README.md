# Trajectory Remix

Trajectory Remix is a local studio for authoring, replaying, generating and
exporting **Kidra trajectories**: multi-window, tick-based transcripts used to
train Project Kidra models.

With it you can:

- **Author** trajectories by hand in a browser dashboard, or through an MCP client
  such as an AI coding agent.
- **Replay** them tick by tick to see exactly what every window contains.
- **Simulate tools** (files, clock, memory, web research, subagents and more)
  so tool results are real, deterministic and reproducible.
- **Generate** new samples from scenario templates using your own model endpoint,
  then review and repair them.
- **Export** approved samples as token chunks for training.

Everything runs on your machine. It needs Python 3.11+, and there are no accounts,
Node build, Docker daemon or external assets. The dashboard listens only on
`127.0.0.1`.

## Quick start

```bash
cd trajectory-remix
./scripts/install.sh            # creates .venv and installs everything
source .venv/bin/activate       # Windows: .venv\Scripts\activate
```

Create a project and start the dashboard. This uses the bundled demo tokenizer,
which is fine for a first look:

```bash
python -m trajectory_remix init \
  --tokenizer examples/demo-tokenizer.json --dataset demo.sqlite
python -m trajectory_remix --dataset demo.sqlite
```

Open **http://127.0.0.1:8765**, then choose **File → Import** and pick
`examples/reactive-sample.json` to get a sample to explore.

For real work, initialize the project with your actual training tokenizer
(`--tokenizer /path/to/tokenizer.json`). See [Getting started](docs/GETTING-STARTED.md)
for details on tokenizers, special tokens and command-line options.

## Documentation

| I want to… | Read |
| --- | --- |
| Install, create a project, choose a tokenizer | [Getting started](docs/GETTING-STARTED.md) |
| Learn the dashboard: replay, timeline, events, search | [Dashboard guide](docs/DASHBOARD.md) |
| Understand the trajectory source format and tick rules | [Replay format](docs/REPLAY-FORMAT.md) |
| Let an AI agent author trajectories over MCP | [MCP setup](docs/MCP.md) and [MCP authoring reference](docs/MCP-AUTHORING.md) |
| Look up a built-in simulated tool | [Tools reference](docs/TOOLS.md) |
| Write my own Python tool, or understand sandbox state | [Sandbox and custom tools](docs/SANDBOX.md) |
| Generate samples, resume, review and repair them | [Scenario generation](docs/SCENARIOS.md) and [Kidra scenarios](docs/kidra-scenarios.md) |
| Check dataset coverage and distributions | [Dataset metrics](docs/METRICS.md) |
| Export tokens and train on them | [Export and training](docs/EXPORT.md) |
| Understand storage, limits and caching | [Project storage and limits](docs/STORAGE.md) |
| Contribute or review the code | [Maintenance guide](docs/MAINTENANCE.md) and [Testing](docs/TESTING.md) |

The [`examples/`](examples/README.md) folder has sample trajectories, a demo
tokenizer and custom-tool examples.

## Concepts in one minute

- A **trajectory** is a JSON document. Its `replay` maps **ticks** (integer time
  steps) to events that open windows, append external text, or have the model
  generate text.
- A **window** is an independent text stream, such as a chat, a reasoning scratchpad,
  or a tool call. Several windows can generate at the same tick.
- **Tools** run in a per-trajectory **sandbox**. The model sees tool output, never
  the sandbox state itself.
- A **project** is one SQLite file holding trajectories, the tokenizer and settings.
  Every accepted edit is saved immediately.
- Only trajectories marked **Ready** are included in token exports.

## Using the MCP server

Install the MCP extra (already done by `scripts/install.sh`) and keep the dashboard
running. MCP clients that support Streamable HTTP, such as the llama.cpp web UI,
connect to `http://127.0.0.1:8765/mcp`. Stdio clients launch
`python -m trajectory_remix.mcp_server --url http://127.0.0.1:8765`.
Full instructions are in [MCP setup](docs/MCP.md).

## Common commands

```bash
# Start the dashboard on another port, with your own tools and scenarios
python -m trajectory_remix --dataset project.sqlite --port 8766 \
  --tools-dir ./custom-tools --scenarios-dir ./scenarios

# Export Ready trajectories as token chunks without opening the dashboard
python -m trajectory_remix --dataset project.sqlite \
  --export token-chunks.jsonl --chunk-ticks 64

# Run the tests
python -m pytest -q
```

## What this project does not include

- No trainer or model checkpoint. Exports and the reference attention adapter
  target Kidra's multi-window execution model, not a generic chat fine-tuning
  collator.
- No real tool execution. Built-in tools are simulations, and memory, skills and
  web pages exist only inside each sample.
- Generation endpoints are tested against mocks, not a live paid model.
