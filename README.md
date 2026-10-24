# Trajectory Remix

Local Kidra trajectory authoring, playback, synthesis and training-export studio.
Python 3.11+. No Node build, external assets, accounts, or Docker daemon required.
The dashboard is served only on `127.0.0.1`.

## Install and start

```bash
cd trajectory-remix
./scripts/install.sh
./scripts/run.sh init --dataset project.trajectories --tokenizer /path/to/tokenizer.json
./scripts/run.sh --dataset project.trajectories
```

Open **http://127.0.0.1:8765** in your browser. Windows activation:
`.venv\Scripts\activate`. Use `--port 8766` to change the port.

A small ASCII character tokenizer and reactive tool sample are included for a
reproducible first run:

```bash
python -m trajectory_remix init \
  --tokenizer examples/demo-tokenizer.json \
  --dataset demo.sqlite --max-window-slots 16
python -m trajectory_remix --dataset demo.sqlite
```

`--dataset` is required. Run `init` once to create the SQLite project and embed
its tokenizer and special-token spellings. Initialization refuses existing files.
Normal startup only opens an existing initialized project; it never creates one.
Each accepted edit commits immediately to that project. `init --max-window-slots N`
fixes the maximum number of distinct windows (including implicit tool windows)
per trajectory. Omit it for an unlimited window count. Startup cannot override it.
Use File > Import to add `examples/reactive-sample.json`.

Use your actual training tokenizer for real datasets. The demo tokenizer is only
a fixture. Initialization reads the tokenizer locally and stores its complete
JSON in the project. Startup and export load that embedded tokenizer; no original
tokenizer file or tokenizer command-line options are needed or accepted.
To use a different tokenizer, initialize a separate project and import trajectories.
Padding and truncation are disabled when compiling trajectories. Required special tokens must each already map to
one dedicated ID. They are never silently added or renumbered.

```bash
python -m trajectory_remix init --dataset custom.sqlite --tokenizer tokenizer.json \
  --bos '<s>' --eos '</s>' --noop '<|noop|>'
```

The process owns the dataset session. Closing the browser does not stop an active
generation job. Stop the job in the dashboard or terminate the server.

## Connect an MCP client

Install the MCP extra in your virtual environment and keep the dashboard running
with your initialized project:

```bash
python -m pip install -e '.[mcp]'
python -m trajectory_remix --dataset project.sqlite
```

For **Streamable HTTP**, including llama.cpp Web UI, connect to
**`http://127.0.0.1:8765/mcp`** (the root URL serves the dashboard). The HTTP
endpoint is available when the MCP extra is installed; restart after installation.
For a browser UI on port 8123, explicitly allow its origin when starting Remix:

```bash
python -m trajectory_remix --dataset project.sqlite \
  --mcp-allow-origin http://127.0.0.1:8123
```

In llama.cpp, select `streamable_http` and use the full `/mcp` URL. Its
`--ui-mcp-proxy` may remain enabled. If you browse via `localhost`, allow
`http://localhost:8123` instead, or repeat the flag to allow both origins.
See the [HTTP connection and curl checks](docs/MCP.md#streamable-http-and-llamacpp-web-ui).

For **stdio** clients, add this server configuration, replacing the Python path
with the absolute path to your virtual environment:

```json
{
  "mcpServers": {
    "trajectory-remix": {
      "command": "/absolute/path/trajectory-remix/.venv/bin/python",
      "args": [
        "-m", "trajectory_remix.mcp_server",
        "--url", "http://127.0.0.1:8765"
      ]
    }
  }
}
```

On Windows, use `.venv/Scripts/python.exe`. Match `--url` to the dashboard's port
if you changed it. The client launches the stdio bridge; its `--url` points to
the dashboard root, while Streamable HTTP clients use `/mcp`. Call `get_session` and
`get_authoring_guide` to check the connection and read the authoring workflow.

Both transports expose the same 42 tools for trajectory creation, full JSON inspection,
duplication, metadata updates, event editing and content patches, validation,
window history, anchors, timeline snapshots, atomic batches, source diffs,
undo/redo, simulated-tool sessions and saving. It shares the running
dashboard session. Edits appear in the dashboard on its next refresh; pending
browser edits are protected by revision checks. Edits commit immediately;
`save_dataset` reports a checkpoint.

**Edit → Undo / Redo** also restores source edits made through MCP, the dashboard
or generation. Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z (or Ctrl/Cmd+Y) invoke dataset history
outside text editors; focused editors retain their native text undo behavior.

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
   EOS. Tool calls have separate duration bars; hover for their arguments and
   start/completion ticks. Scroll horizontally and change zoom.
4. **Events** edits tick expressions, operations, window IDs, source, delivery
   rate and loss masks. Use **+ Add event**, reorder, or remove an event. The
   **+ EOS** button inserts the configured EOS spelling. A normal explicit
   `close` operation is rejected. Segmented content is edited in Source JSON.
   **+ Tool call** schedules a synthetic tool with JSON arguments, a duration in
   ticks, and a tool window ID. Tool windows exist from tick 0; each contains
   both the request and result. Requests always stream one token per tick, with
   optional custom text, NOOPs and anchors. **Result delivery** selects instant or
   streamed results. **Auto-generate post-result anchor** assigns a marker for
   scheduling later events after the full result arrives.
5. **Source JSON** supports the entire canonical schema, including segmented
   losses and optional metadata. **Apply & compile** saves the draft even if
   compilation fails, so errors can be repaired without losing the work.
6. Browser drafts are committed to SQLite automatically after the compile delay;
  applied edits and accepted generated trajectories are durable immediately.
7. **Export tokens** exports the selected sample or whole dataset as chunked
   token JSONL. Every selected sample must compile before export begins.

The **Trajectory metadata** section at the bottom of the dataset sidebar edits
the selected trajectory's `metadata`. Set **Agent name** (`agent_name`) or
**User name** (`user_name`), or expand **All properties (JSON)** to view and edit
other properties, including nested objects, tags and synthetic clock settings.
Unrelated metadata is preserved. These edits synchronize with Source JSON and
use the same automatic apply, save and Undo/Redo workflow as event edits.
Leaving a name blank omits that property; the default names are Kidra and User.

Dataset files are SQLite databases, conventionally named `project.sqlite`.
The `project` row contains configuration, schema/revision counters, tokenizer JSON
and special-token spellings (`bos`, `eos`, `noop`), the window-slot limit and a
versioned allocation policy. The tokenizer is fixed at init;
configuration edits and trajectory imports do not change it.
Trajectories are stored as individual JSON records in rows, with indexed unique
UUID primary keys and separate listing metadata. The editable `id` remains a
unique label; `uuid` is immutable, survives edits/undo/imports, and seeds slot
allocation. Duplicating a trajectory creates a new UUID. Legacy imports without
UUIDs derive one deterministically from their ID (or allocate one for unnamed
drafts). The server fetches trajectories on demand;
it does not load or copy the whole dataset. The sidebar searches in SQLite and
shows 100 results per page. Undo history and compiled previews are bounded.

Writes use WAL mode, a 30-second busy timeout and transactions. Multiple server
processes can share a local database: revisions detect competing edits, and
unique constraints prevent duplicate IDs. SQLite serializes writers while
readers use consistent snapshots. Keep the database on a local filesystem;
WAL databases can have companion `-wal` and `-shm` files. Use SQLite's backup API
or stop all writers before copying a database.

Existing `.json`, `.jsonl`, and JSON `.kidra-dataset` files can still be imported.
To use an old project, initialize a new SQLite project with the correct tokenizer,
start that project, then use
**File → Import**. Legacy JSON files are never overwritten at startup.
Bulk replacement imports clear session undo history so the old dataset does not
have to be loaded into RAM. Token exports remain JSONL and process one trajectory
at a time, spooling to disk before download so invalid trajectories cannot cause
a partial export.

API keys are excluded. Datasets preserve prompts, task order, endpoint URLs/model
names, generation settings, sample metadata, tags and loss controls. The tokenizer
is embedded at initialization; token exports contain its SHA-256 fingerprint.
Marker timings change with tokenizer tokenization.

Reload a saved dataset at startup:

```bash
python -m trajectory_remix --dataset project.sqlite
```

## Metadata queries

`trajectory_metadata` has one row keyed by the trajectory UUID. It stores metadata
properties as structured JSON, a dedicated tag list, and cached event/window/tag
counts. `metadata_tags` indexes distinct tag membership. Arbitrary metadata keys
remain flexible; adding a new key does not alter the database schema. Numeric
counts are JSON numbers, not strings. Objects and arrays are preserved.

Every trajectory also has a canonical `content_hash` for invalidating future
statistics caches per UUID. Source, hash, metadata projection and tag index update
in the same transaction, including undo/redo. Deletion cascades to metadata/tags.

`POST /api/metadata/query` (MCP `query_trajectories`) supports typed scalar filters,
tag membership, and pagination without fetching replay payloads:

```json
{
  "filters": [
    {"key": "quality", "op": "gte", "value": 0.8},
    {"key": "counts.windows", "op": "lte", "value": 16}
  ],
  "tags": ["reviewed"],
  "tag_mode": "all",
  "offset": 0,
  "limit": 100
}
```

Filters support `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `exists`. Keys are literal
metadata property names; reserved `counts.events`, `counts.windows`, `counts.tags`
address indexed counters. Missing keys do not match comparisons; explicit JSON
null matches `eq: null`. Booleans remain distinct from numbers. Filters combine
with AND; `tag_mode` accepts `all` or `any`.

`POST /api/metadata/counts` (MCP `get_metadata_counts`) groups matching trajectories
by a scalar metadata key, cached counter, or `tags`, with a bounded number of
value/count groups. Each trajectory counts once per distinct tag. For example,
`{"key":"domain","tags":["reviewed"]}` gives reviewed trajectory counts by domain.
Tag/counter lookups have indexes; arbitrary property filters currently scan the
small metadata JSON projection in SQL. Add field-specific indexes when the
actual statistics/query workload is known.

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

- Phase A opens windows and inserts exactly one BOS followed by an automatic
  `<|cxt|>Window: 4\nPurpose: tool<|/cxt|>` header (with an actual newline).
  Both are durable conditioning context excluded from loss. The header uses the
  window's numeric slot and `window-type` (default `document`), including for
  implicitly opened tool windows. Initialization is atomic at the opening tick;
  it consumes no extra ticks and does not move content anchors.
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
- Use `{WINDOW:tool0}` in authored `content` or segment `text` to refer to a
  window by its stable logical ID. Compilation/export replaces it with that
  window's numeric slot, e.g. `4`, before tokenization and anchor resolution.
  Source JSON retains the marker. `\{WINDOW:tool0}` preserves literal text;
  malformed markers and references to undeclared windows are errors. References
  may target a later declaration or an implicit tool window. Tool arguments and
  actual sandbox observations remain literal data.
- New projects use `uuid-hash-v1`: a SHA-256 stream seeded by the immutable
  trajectory UUID assigns unique slots within the project's configured slot space.
  Allocations are independent of process randomness, metadata and label edits.
  Canonical numeric-then-symbolic replay keys and list order determine window
  declaration order. References, headers and token exports share the mapping.
  Slots are not reused. Exceeding `max_window_slots` rejects creation/import,
  edits, generation and export, including implicit tool windows. With an unlimited
  budget the slot space is the number of declared windows, so adding windows can
  change mappings; use named markers in source.
- Headers appear in replay, task context and token exports. The tokenizer is
  never modified: context delimiters use existing dedicated tokens when present,
  otherwise ordinary tokenizer-native text tokens. Header metadata escapes
  newlines and embedded control/framing tokens. To reproduce legacy body-only
  conditioning, set `defaults.window-headers` to `false` (default `true`).
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
file and requests to 40 MB. Large imports should be split into batches; the SQLite dataset itself has no
100,000-trajectory limit.

## LLM tasks and endpoint configuration

**Configuration → Endpoints** defines independent OpenAI-compatible services.
Set base URL including `/v1`, model ID, temperature, completion-token limit,
timeout, and optional API-key environment variable or session-only key.

```bash
export REMIX_GENERATOR_KEY='your-key'
export REMIX_VALIDATOR_KEY='your-key'
python -m trajectory_remix --dataset project.sqlite
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
Tool call arguments become supervised `<tool>{"tool":...,"args":...}</tool>`
text and actual dispatcher results become masked `<tool_result>...</tool_result>`
text in the same tool window, open from tick 0. Results remain open through downstream
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
- `autosave_every`: accepted samples between checkpoint progress reports; every
  accepted sample commits immediately.
- `delay_seconds`: delay between candidate attempts (minimum 0.1 seconds).
- `max_consecutive_failures`: hard stop threshold, even with skip/pause chosen.
- `failure_action`: stop immediately, pause after a failed attempt, or skip to
  another candidate. Compilation, HTTP, empty response and validator failures
  use the same failure mechanism. A new acceptance resets the failure streak.
- `regenerations`: retries with concrete compiler/validator feedback inserted
  into each task prompt. Each candidate receives a fresh isolated sandbox.
- `validate`: run enabled LLM validators before accepting a candidate.

Accepted samples enter the dataset immediately. A stop cancels the in-flight HTTP
request and discards the incomplete candidate. Accepted changes are already durable. Pause
finishes the in-flight request and blocks at the next pipeline/attempt boundary;
it is not a provider-side pause. Generator state/quotas are per candidate, and
credentials are not included in retained datasets. Generation locks dataset
editing/configuration to prevent index races. Downloads and checkpoints remain
available.

## Tools and sandbox state

The sandbox is an initially empty dictionary owned by tools. Each tool lives in
its own module under `trajectory_remix/tools/` and defines a name, JSON argument
schema, and `run(args, state, windows) -> (output, is_done, modified_state)`.
The compiler runs unfinished calls every tick, caches state only on reported
mutations, and recalculates that history on recompilation. Kidra sees tool
output and windows; the dictionary is author-facing only.

The **Sandbox** panel follows the selected tick and displays arbitrary state and
separate call status. Tools can inspect open windows and modify their content;
window names and purposes remain immutable. Invisible calls may use schema
properties marked `invisible`, which public schemas omit. For example, an
invisible `chat` call can supply `username` to simulate a user message.
Visible calls, including MCP calls, reject invisible arguments. Tools may declare
`invisible = True` to restrict the entire tool to invisible calls; these tools
never appear in public schemas or `show_tools`, including random additions.

**+ Show tools** adds a `show_tools` event. Check the tools whose public schemas
should be appended to an open window. **Randomly add additional tools** adds a
repeatable random selection of other tools for training diversity. Explicitly
selected tools always remain included.

Start with custom tools using:

```bash
python -m trajectory_remix --dataset project.sqlite --tools-dir ./custom-tools
```

Remix loads every Python file in that folder as one uniquely named tool. SQLite
records external names, schemas, hashes, and trajectory dependencies. Restarting
with changed files recompiles affected trajectories. Missing tools and changed
schemas produce compilation errors. Custom modules are trusted server-side
Python; the built-in tools remain local simulations.

See [the tool contract and custom module example](docs/SANDBOX.md) for timing,
sparse caching, window writes, invisible arguments, schema compatibility, and
built-in conventions. The [MCP reference](docs/MCP.md) covers authoring commands;
`create_event` can insert `show_tools`, and `create_tool_call` schedules visible
or invisible executions. Frame and sandbox API responses keep `sandbox` and
`tool_calls` separate so tool-owned keys never collide with scheduler metadata.

The built-in selection also includes a `simpleeval` calculator, reproducible
random draws, tick timers and synthetic-clock alarms, virtual file watchers,
and simulated system diagnostics. Invisible `system_diagnostics_set` and
`hardware_load_set` events configure diagnostics and extra file-operation ticks.
See [additional simulation tools](docs/SANDBOX.md#additional-simulation-tools)
for arguments, timing, and examples.

## Headless export and tests

```bash
python -m trajectory_remix --dataset samples.sqlite --export token-chunks.jsonl --chunk-ticks 64
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
| `dataset.py` | JSON import normalization, trajectory defaults and key removal. |
| `storage.py` | SQLite storage, lazy reads, paginated metadata and sparse edits. |
| `llm.py` | Config/task defaults, prompt organization, bounded HTTP, generation, validators. |
| `tools/` | Tool registry, tick protocol, and one module per built-in tool. |
| `sandbox_replay.py` | Generic tick scheduling, window writes, and sparse state snapshots. |
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
logical token ticks. Executable tool-call results use instant or streamed delivery;
ordinary append events can stream manually authored observations with inline
anchors when authoring reactive trajectories.

Primary API/security references: [Hugging Face Tokenizers](https://huggingface.co/docs/tokenizers/en/api/tokenizer).
