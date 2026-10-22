# Trajectory Remix

Local Kidra trajectory authoring, playback, synthesis and training-export studio.
Python 3.11+. No Node build, external assets, accounts, or Docker daemon required.
The dashboard is served only on `127.0.0.1`.

## Install and start

```bash
cd trajectory-remix
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m trajectory_remix --tokenizer /path/to/tokenizer.json
```

Open **http://127.0.0.1:8765** in your browser. Windows activation:
`.venv\Scripts\activate`. Use `--port 8766` to change the port.

A small ASCII character tokenizer and reactive tool sample are included for a
reproducible first run:

```bash
python -m trajectory_remix \
  --tokenizer examples/demo-tokenizer.json \
  --dataset demo.kidra-dataset
```

`--dataset` is required: the file is created if missing and is the only save
target. Use File > Import to add `examples/reactive-sample.json`.

Use your actual training tokenizer for real datasets. The demo tokenizer is only
a fixture. The Hugging Face `tokenizers` library loads the tokenizer locally;
no tokenizer is uploaded or downloaded. Padding and truncation are disabled
when compiling trajectories. Required special tokens must each already map to
one dedicated ID. They are never silently added or renumbered.

```bash
python -m trajectory_remix --tokenizer tokenizer.json \
  --bos '<s>' --eos '</s>' --noop '<|noop|>'
```

The process owns the dataset session. Closing the browser does not stop an active
generation job. Stop the job in the dashboard or terminate the server.

## MCP support

Install `python -m pip install -e '.[mcp]'`, start the dashboard normally, then
connect an MCP client to the stdio command:

```bash
python -m trajectory_remix.mcp_server --url http://127.0.0.1:8765
```

The bridge exposes 24 tools for trajectory creation, full JSON inspection,
duplication, metadata updates, event editing and content patches, validation,
window history, anchors, timeline snapshots and saving. It shares the running
dashboard session. Edits appear in the dashboard on its next refresh; pending
browser edits are protected by revision checks. Use `save_dataset` to persist.

See [MCP setup and tool reference](docs/MCP.md) for client configuration,
examples, validation options and limits.

## Dashboard workflow

1. **Import samples** accepts multiple `.json`, `.jsonl`, and `.kidra-dataset`
   files in one batch. JSON may contain one trajectory, an array, or a `samples`
   array. JSONL contains one trajectory per nonempty line. Import appends to
   the current session.
   Import is atomic:
   malformed JSON or duplicate sample IDs rejects the batch without changing
   existing data. Structurally invalid trajectories remain editable and show
   diagnostics when compiled. A dataset's configuration is restored on import.
2. **Replay** displays the post-commit window state at the selected tick. Step
   forward/back, scrub, play, or jump to a tick. Space toggles playback; arrow
   keys step when an editor is not focused. Closed windows can remain visible
   for inspection even though their KV is unavailable to later model decisions.
3. **Timeline** shows window lifetimes, streamed content intervals and anchor
   capture ticks. Generated output is colored by window; external input is
   violet. Pale bars indicate idle/open intervals, and vertical end caps indicate
   EOS. Hover for event information. Scroll horizontally and change zoom.
4. **Events** edits tick expressions, operations, window IDs, source, delivery
   rate and loss masks. Use **+ Add event**, reorder, or remove an event. The
   **+ EOS** button inserts the configured EOS spelling. A normal explicit
   `close` operation is rejected. Segmented content is edited in Source JSON.
5. **Source JSON** supports the entire canonical schema, including segmented
   losses and optional metadata. **Apply & compile** saves the draft even if
   compilation fails, so errors can be repaired without losing the work.
6. **Save** (Ctrl+S) writes the server's `--dataset` project file. Generation
   also saves every configured N accepted samples and on stop.
7. **Export tokens** exports the selected sample or whole dataset as chunked
   token JSONL. Every selected sample must compile before export begins.

Dataset files are UTF-8 JSON, not executable archives:

```json
{
  "format": "trajectory-remix/kidra-dataset/1",
  "samples": [],
  "config": {"endpoints": [], "tasks": []}
}
```

API keys are excluded. Datasets preserve prompts, task order, endpoint URLs/model
names, generation settings, sample metadata, tags and loss controls. The tokenizer
is an explicit startup input and is not embedded; token exports contain its
SHA-256 fingerprint. Marker timings change with tokenizer tokenization.

Reload a saved dataset at startup:

```bash
python -m trajectory_remix --tokenizer tokenizer.json \
  --dataset project.kidra-dataset
```

## Replay contract

This implementation follows **Project Kidra — Multi-Window Trajectory Replay
Specification, version 1.0, 20 October 2026**, recovered from the saved project
file when the new attachment was unavailable. It implements the proposed design
contract; it is not an audit of the current ProjectKidra repository.

```json
{
  "schema-version": "1.0",
  "id": "warning-example",
  "tick-policy": "external-first",
  "defaults": {"implicit-noop-loss-weight": 0.1},
  "replay": {
    "0": [
      {"op": "open", "window": "tool", "window-type": "tool"},
      {"op": "open", "window": "reasoning", "window-type": "reasoning"}
    ],
    "1": [
      {"op": "append", "window": "tool", "source": "tool", "delivery": "stream",
       "content": "Start. WARNING!{N_TICK:4} Retrying... Done.{N_TICK:5}",
       "loss": {"mask": "exclude"}}
    ],
    "{N_TICK:4}+1": [
      {"op": "generate", "window": "reasoning", "content": "A warning arrived.<|eos|>"}
    ],
    "{N_TICK:5}+1": [
      {"op": "generate", "window": "tool", "content": "<|eos|>"}
    ]
  }
}
```

- Phase A opens windows and inserts exactly one BOS, excluded from loss.
- Phase B applies external appends, instantaneous or streamed. Instant is the
  append default; streamed appends default to one token per tick and may set
  `tokens-per-tick` to a positive integer.
- Phase C makes model decisions from the shared snapshot after phase B.
  Generated streams emit one token per window per tick.
- Phase D commits model outputs, closes EOS windows and releases live KV.
  Other windows cannot read same-tick generated outputs.
- A window with no incoming content or explicit target emits implicit NOOP at
  the configured loss weight. NOOP never enters durable content/KV. The replay
  ends at the final scheduled tick; open windows are allowed and reported.
- External content is excluded from loss by default, but remains conditioning
  context. `loss.mask = include / exclude / auto` and finite nonnegative
  `loss.weight` override supervision. Generated EOS has normal target loss.
- Only generated EOS closes a window. UI and task authoring insert its token
  into a generate event; the compiler never silently closes an unfinished
  imported window or invents a closure time. IDs cannot be reused.
- `{N_TICK:n}` is zero-width and may occur anywhere in a content stream.
  `{N_TICK:n}+k` is a replay-key expression with nonnegative offset. The spelling
  `{N_TICKS}` is not the canonical syntax and receives an error.
- An anchor captures the tick when its preceding prefix commits. At an empty
  prefix it captures the event start boundary. All instant append anchors
  capture that append's start tick. Duplicate, undefined, cyclic and malformed
  anchors are errors. An anchor that splits a tokenizer token is an error.
  Tokenization is performed once on the entire marker-free content, not each
  fragment. `\{N_TICK:4}` preserves literal marker text.
- A phase-D capture cannot trigger a same-tick event. A phase-B capture can
  trigger same-tick generation in another window, but opening or appending from
  that capture requires `+1`. Use `+1` for portable reaction schedules.
- Simultaneous independent windows are supported. Overlapping writers to one
  window are rejected, except multiple instantaneous external appends, which
  concatenate in declared list order. Same-tick open must precede content.
- For equal resolved ticks from distinct replay keys, numeric keys then symbolic
  keys use stable sorted-key order; list order within each key is preserved.
- Segmented masks use `segments: [{text, loss}, ...]` instead of `content`.
  Every segment boundary must also be a tokenizer-token boundary.

Defaults: at most 100,000 ticks and 500,000 expanded rows per compiled sample.
Interactive previews are limited to 50,000 rows. Imports are limited to 32 MB per
file and requests to 40 MB. Very large datasets should be split into batches.

## LLM tasks and endpoint configuration

**Configuration → Endpoints** defines independent OpenAI-compatible services.
Set base URL including `/v1`, model ID, temperature, completion-token limit,
timeout, and optional API-key environment variable or session-only key.

```bash
export REMIX_GENERATOR_KEY='your-key'
export REMIX_VALIDATOR_KEY='your-key'
python -m trajectory_remix --tokenizer tokenizer.json
```

Environment variables are read by the backend only. A blank key field preserves
an existing session key; **Clear stored session key** removes it. Saved datasets
never include key values. Host environment credentials are not exposed to tools.
Endpoints are contacted only when generation or validation is started. No
credentials or provider access were available during development; automated
integration tests use deterministic OpenAI-compatible mocked responses.

Each **Task** has its own written prompt, endpoint, visibility selection,
organization, and enabled state. Built-ins cover system prompt, tagged user
request, reasoning/tool use, assistant response, user followup, followup response,
and validation. Duplicate/add/reorder tasks to create more content streams or
multiple validators. Every task produces a separate stream.

Visibility accepts ordered comma-separated window IDs or `*`. Layouts:

- **Separate labeled messages:** one data message per selected window.
- **Single labeled transcript:** combine selected windows in one data message.
- **Structured JSON:** serialize the selected windows into a JSON object.

Task prompts remain privileged instructions. Imported window text is labeled
as input data, including source system prompts. The API supports a task's
optional `visible_tick` to prefix-limit conditioning. Without it, all selected
windows are supplied through the current replay end, including closed-window
transcripts for offline authoring/validation. The replay attention predicate
still removes closed-window KV during actual execution.

Generation tasks specify append/generate ownership, source, target window,
loss mask, optional EOS closure, sandbox tools, and start tick/expression.
`auto` schedules after the existing replay end. Explicit starts can overlap
independent windows. For an explicit early start, task conditioning is
automatically limited to external inputs available by that tick and generated
outputs committed before that tick. Optional `visible_tick` can constrain it
further. Generated text still needs semantic validation: prefix limiting
prevents showing future observations but does not prove model correctness.

**Run this task in current sample** fills a selected existing sample, replacing
it only after compilation succeeds; the UI skips semantic validation for this
single-stream edit. A closed window cannot receive new content: choose a fresh
target ID or remove its prior EOS first. **Generate sample** runs all enabled
generation tasks into a new sample. Enable followup tasks to include further
turns.

Tools use standard `tools` / `tool_calls` on `POST /chat/completions`.
Tool call arguments become supervised call-window text and actual dispatcher
results become masked tool-result windows. Results remain open through downstream
reasoning and responses, then receive generated EOS. Tool-enabled tasks require
`start=auto`; explicitly timed tool interactions can be authored in the replay. Non-tool providers work with tools
disabled. Models must return ordinary textual completion content. Truncated,
empty and malformed completions count as failures.

Validators must return exactly the useful shape:

```json
{"valid": false, "feedback": ["The response gives a result the tool did not return."]}
```

All enabled validators must accept. Compiler validation always runs; LLM
validation is optional. JSON code fences are tolerated. A malformed report
fails rather than passing optimistically.

## Automatic mode

- `max_samples`: accepted new samples; zero means unlimited.
- `max_attempts`: candidate attempts; zero means unlimited. An attempt may run
  `regenerations + 1` complete task pipelines.
- `autosave_every`: accepted samples between atomic disk checkpoints.
- `delay_seconds`: delay between candidate attempts (minimum 0.1 seconds).
- `max_consecutive_failures`: hard stop threshold, even with skip/pause chosen.
- `failure_action`: stop immediately, pause after a failed attempt, or skip to
  another candidate. Compilation, HTTP, empty response and validator failures
  use the same failure mechanism. A new acceptance resets the failure streak.
- `regenerations`: retries with concrete compiler/validator feedback inserted
  into each task prompt. Each candidate receives a fresh isolated sandbox.
- `validate`: run enabled LLM validators before accepting a candidate.

Accepted samples enter the dataset immediately. A stop cancels the in-flight HTTP
request, discards the incomplete candidate and flushes accepted changes. Pause
finishes the in-flight request and blocks at the next pipeline/attempt boundary;
it is not a provider-side pause. Generator state/quotas are per candidate, and
credentials are not included in retained datasets. Generation locks dataset
editing/configuration to prevent index races. Downloads and checkpoints remain
available.

## Tool sandbox

Built-in tools run as an allowlisted capability interpreter over fresh RAM
state. Model text is never executed. They cannot access host files, process
execution, Docker, dynamic modules, URLs, or arbitrary networks. No eval, exec,
subprocess or filesystem API is exposed through a tool.

| Tools | Behavior |
|---|---|
| `time_now`, `time_advance` | Per-sample synthetic ISO clock; configurable seed and automatic increment per call. No real sleeping. |
| `file_write/read/list/delete` | Virtual UTF-8 files inside a RAM dictionary; traversal, NUL and backslashes rejected. `/etc/x` is merely a virtual path. |
| `memory_save/search` | Local lexical-overlap retrieval with top-8 results. RAG store is isolated; no embeddings service required. |
| `note_save/list/delete` | Tasks/notes with todo, doing, done status. |
| `skill_save/load/list` | Skill texts stored and retrieved within the sample only; not executed. |
| `subagent` | One attached-LLM task in a fresh child sandbox; no parent memory or host access. Child tool calling is disabled. |

2 MB total state and 200k characters per string; configurable bounded call and
round budgets. Inputs and tool names are strictly checked; unknown tools return
errors. Results are actual simulation outcomes, not fabricated claims of real
host actions. State is destroyed in `finally` blocks on success, failure and
cancellation. The subagent's outbound capability is the already configured LLM
broker, not general network access.

This is a **capability sandbox for fixed simulated tools**, not a process-level
sandbox for arbitrary third-party code. Adding Python/shell/plugin execution
would require a separate hardened execution service. Docker provides namespaces
and resource isolation but shares the host kernel; default containers alone
are not a sufficient hostile-code boundary. Such an extension should use an
unprivileged/rootless runtime, no Docker socket/host mounts, no network, read-only
root filesystem, dropped capabilities, seccomp, resource limits and, where
needed, stronger isolation such as a microVM. None of that is needed to run the
included simulation interpreter.

The HTTP service also enforces a random per-process session token on API calls,
trusted local Host values, same-origin checks and a content security policy.
It is a local single-user tool, not a multi-user/public hosting service.

## Headless export and tests

```bash
python -m trajectory_remix --tokenizer tokenizer.json \
  --dataset samples.kidra-dataset --export token-chunks.jsonl --chunk-ticks 64
python -m pytest -q
```

Optional frontend DOM integration checks (Node 22+):

```bash
npm install --prefix tests
python tests/run_frontend.py
```

These exercise the actual dashboard JavaScript against a temporary local backend
in an emulated DOM. They do not replace visual testing in a real browser.

The tests cover timeline causality, tokenizer alignment, masks, lifecycle,
parallel windows, chunk reconstruction, API import/export, key redaction,
sandbox capabilities/quotas, tool-grounded generation, validation feedback,
cancellation and failure limits. `docs/EXPORT.md` describes the training adapter.

## Source map

| File | Responsibility |
|---|---|
| `compiler.py` | Schema, anchors, timing, lifecycle, token rows, masks, exact attention predicate. |
| `training.py` | Export reader, reference query/attention batching, optional PyTorch conversion. |
| `dataset.py` | Import normalization, dataset shape, atomic persistence, key removal. |
| `llm.py` | Config/task defaults, prompt organization, bounded HTTP, generation, validators. |
| `tools.py` | Isolated RAM capability sandbox and tool schemas. |
| `runner.py` | Background generation, retry/feedback, pause/stop, autosaves. |
| `server.py` | Local API and dashboard serving. |
| `static/` | Dependency-free dashboard, editor, timeline and configuration UI. |

## Implementation boundaries

No trainer or model checkpoint is bundled. Exports and a reference attention
adapter target Kidra's multi-window execution contract, not a generic chat SFT
collator. Endpoint integration is tested with mocks, not a paid/live model.
Skill persistence and memory retrieval are simulated inside each sample; they
do not modify the real user's skills or external RAG databases. Generation API
responses are collected per task, while the resulting sample is replayed at
logical token ticks. Tool results generated automatically use instant delivery;
edit them to stream with inline anchors when authoring reactive trajectories.

Primary API/security references: [Hugging Face Tokenizers](https://huggingface.co/docs/tokenizers/en/api/tokenizer), [Docker Engine security](https://docs.docker.com/engine/security/).
