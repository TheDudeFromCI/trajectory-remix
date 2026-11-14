# Getting started

This page covers installation, creating a project, choosing a tokenizer, and the
command-line options. Once the dashboard is open, continue with the
[Dashboard guide](DASHBOARD.md).

## Install

```bash
cd trajectory-remix
./scripts/install.sh
```

The script creates `.venv` and installs the package with its test and MCP extras.
Activate the environment with `source .venv/bin/activate` (Windows:
`.venv\Scripts\activate`). `./scripts/run.sh <args>` runs
`python -m trajectory_remix <args>` inside that environment; with no arguments
it opens `demo.sqlite`.

## Create a project

A **project** is a single SQLite file. Create it once with `init`:

```bash
python -m trajectory_remix init \
  --tokenizer /path/to/tokenizer.json \
  --dataset project.sqlite
```

`init` reads your Hugging Face `tokenizer.json` and stores a complete copy inside
the project, so later startups and exports need neither the original file nor any
tokenizer options. It refuses to overwrite an existing file, and normal startup
never creates a project.

Optional `init` flags:

| Flag | Meaning |
| --- | --- |
| `--max-window-slots N` | Maximum number of distinct windows per trajectory, including implicit tool windows. Omit for unlimited. Fixed at creation. |
| Special-token flags | Override the spellings of the special tokens (see below). |

> **Use your real training tokenizer.** `examples/demo-tokenizer.json` is a tiny
> character-level fixture for trying things out. Timings in sample trajectories
> depend on the tokenizer, so imported examples may need retiming with a different
> one.

## Start the dashboard

```bash
python -m trajectory_remix --dataset project.sqlite
```

Open **http://127.0.0.1:8765**. Add `--port 8766` to change the port. Closing the
browser tab does not stop a running generation job; stop it in the dashboard or
terminate the server.

| Option | Purpose |
| --- | --- |
| `--dataset FILE` | Required. The project to open. |
| `--port N` | Dashboard port (default 8765). |
| `--tools-dir DIR` | Load custom Python tools. See [Sandbox and custom tools](SANDBOX.md#custom-python-tools). |
| `--scenarios-dir DIR` | Replace the bundled generation scenarios with your own. See [Scenario generation](SCENARIOS.md#custom-scenarios). |
| `--mcp-allow-origin URL` | Allow a browser-based MCP client at this origin. See [MCP setup](MCP.md). |
| `--export FILE` | Write a training export and exit. See [Export](EXPORT.md). |
| `--chunk-ticks N` | Ticks per export chunk (default 64). |

In **Edit → Preferences** you can choose the default split (Train, Development or
Test) for new manual and generated trajectories. It defaults to Train, and existing
trajectories keep their splits.

## Special tokens

Remix needs a few special tokens, each of which must already be a single dedicated
token in your tokenizer. They are never added or renumbered for you.

| `init` flag | Default spelling | Role |
| --- | --- | --- |
| `--bos-token` | `<\|bos\|>` | Start of a window |
| `--eos-token` | `<\|eos\|>` | End of generation; closes a window |
| `--noop-token` | `<\|noop\|>` | "Nothing to say this tick" target |
| `--tool-token` / `--tool-end-token` | `<tool>` / `</tool>` | Wrap a tool request |
| `--tool-result-token` / `--tool-result-end-token` | `<tool_result>` / `</tool_result>` | Wrap a tool result |
| `--msg-token` / `--msg-end-token` | `<\|msg\|>` / `<\|/msg\|>` | Wrap a chat message |
| `--ctx-token` / `--ctx-end-token` | `<\|ctx\|>` / `<\|/ctx\|>` | Wrap automatic window headers |

Omit a flag to keep its default. For example:

```bash
python -m trajectory_remix init --dataset custom.sqlite --tokenizer tokenizer.json \
  --bos-token '<kidra_bos>' --eos-token '<kidra_eos>' --noop-token '<kidra_noop>' \
  --tool-token '<kidra_tool>' --tool-end-token '</kidra_tool>'
```

`--bos`, `--eos` and `--noop` still work as aliases. The tool, message and context
delimiters are visible conditioning context. If your tokenizer has no dedicated
tokens for them, Remix falls back to ordinary text tokens, so it works with
unmodified tokenizers. Spellings are saved with the project and shown in
`get_session` and in export records.

Padding and truncation are disabled when compiling trajectories.

## Change a project's tokenizer

Use **File → Migrate Tokenizer…**:

1. Upload a new `tokenizer.json` or paste its configuration.
2. Check or edit the eleven special-token spellings.
3. Start the migration and watch the converted/total counter.

Migration recompiles every saved trajectory, one at a time, in the background.
Generation must be stopped first, and editing is blocked until it finishes.

- **On success**, the embedded tokenizer is replaced. Source text, timing markers,
  UUIDs and metadata are kept. Undo/Redo history and cached previews are cleared.
  Restart any other server processes that share the project.
- **On failure**, the dialog names the offending trajectory and the diagnostic, and
  the project is left unchanged. Typical causes are marker or segment boundaries
  that no longer fall on token boundaries, or fixed timings that no longer fit.
  Repair those sources and retry.

## Try the examples

| File | What it shows |
| --- | --- |
| `examples/reactive-sample.json` | A small reactive tool sample. Import it with **File → Import**. |
| `examples/build-graph-analysis.json` | A complete worked example: six virtual Python modules, concurrent file tools, and separate reasoning and chat windows. |

The build-graph example was timed for a different tokenizer, so with the demo
tokenizer its generation durations will not match until retimed. MCP agents can
retrieve it with `get_authoring_guide(topic="example")` without importing it.

## Move an older project

Existing `.json`, `.jsonl` and JSON `.kidra-dataset` files can be imported. Create
a new SQLite project with the correct tokenizer, start it, then use
**File → Import**. Old files are never overwritten.

## Next steps

- [Dashboard guide](DASHBOARD.md): replay, timeline, event editing and search.
- [Replay format](REPLAY-FORMAT.md): what the source JSON means.
- [MCP setup](MCP.md): author with an AI agent.
- [Scenario generation](SCENARIOS.md): generate samples with your own model.
