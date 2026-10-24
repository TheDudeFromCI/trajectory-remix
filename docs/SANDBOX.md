# Tools and sandbox state

Every compilation starts with an empty Python dictionary. Remix assigns no
meaning to its keys. Tools own their state conventions; the compiler, training
export, and generation prompts never interpret that dictionary. Kidra sees
window content and tool output only.

A tool is a Python module with `name`, a JSON argument `schema`, and:

```python
def run(args: dict, state: dict, windows: list[WindowProperties]) -> tuple[str, bool, bool]:
    return output, is_done, modified_state
```

Execution begins on the tick after the final request token. Invisible calls begin
at their scheduled tick. `run` executes once per tick, in stable source order,
until `is_done` is true. Instant tools finish on their first invocation. Every
nonempty output becomes a masked tool observation at that invocation's tick.
`delivery: "stream"` streams observations using `tokens-per-tick`; a
`result-anchor` captures the final delivered result token. Subagents use this
same scheduler and protocol.

Each `run()` invocation has a 10-second wall-clock deadline. An invocation that
stalls fails with an error identifying the tool and tick; repeated invocations
that return `is_done=False` are still bounded by the 100,000-tick trajectory
limit. A tool may run for many ticks as long as each invocation returns promptly.
Custom tools run in a disposable worker process, which is terminated on timeout;
partial state and window writes from failed invocations are discarded. Keep
persistent progress in the supplied dictionary: module globals are not retained
between invocations. Custom state must be serializable for the worker process.
Built-ins use a lightweight POSIX deadline on the main thread, with worker
isolation on other platforms or threads.

The compiler trusts `modified_state`. It deep-copies state only when this flag
is true, keeping one snapshot per changed tick. For example, snapshots at ticks
1, 8, and 12 describe all intervening ticks without seven redundant copies.
Scrubbing selects the last applicable snapshot and returns an independent copy;
it never executes tools. Recompiling discards and recomputes the state history.
Tool implementations must report every mutation and return deepcopyable state.
Use JSON-compatible values when exposing state through the dashboard or API.

Frames contain separate `sandbox` and `tool_calls` properties. State may itself
use any key, including `tool_calls`, without colliding with scheduler metadata.
The author-facing Sandbox panel displays the dictionary as JSON. Training
exports contain only replay rows and window lifecycle data.

`duration-ticks` remains supported by the built-in simulation tools as a legacy
execution delay. New tools implement their own progress in `run` and normally
omit it. `ExecutionContext`, available through
`trajectory_remix.tools.execution_context.get()`, provides the current tick,
start tick, call ID, author metadata, and compatibility duration. It is optional;
a tool can implement progress entirely within its arguments and sandbox state.

## Windows

`windows` is a list of all currently open windows. Each `WindowProperties` has
read-only `name` and `purpose` properties and a writable text `content` property.
Dictionary access (`window["content"]`) is supported as well. Tools can inspect
other windows, append text, or replace content. Changes are masked external
input in phase B. A replacement removes the previous content from subsequent
frames and attention while retaining earlier history. Automatic BOS/header
context remains available. Same-tick generated output becomes visible on the
next tick, and generated EOS removes a window after phase D.

Window writes and sandbox changes are independent: a tool that only updates a
window should return `modified_state=False`.

## Invisible arguments and external activity

Annotate an argument property with `"invisible": true` (or `"x-invisible": true`).
Public model schemas and `show_tools` omit invisible properties and their required
entries. The full schema remains available to the author in the editor and
`list_simulated_tools`. A visible call that supplies an invisible argument is
rejected, including through MCP draft-editing commands and workbench calls.

Tools may also declare a module-level `invisible = True` flag (default `False`).
These tools can only execute through `invisible-tool-call` events. They are omitted
from model tool lists, the visible-call editor, the `show_tools` checkboxes, and
random additional tools. Explicitly listing one in `show_tools` is a compilation
error. Full author schemas include an `invisible` boolean so invisible-call
editors can still discover and schedule them. Visible MCP calls and draft edits
reject these tools as well. The built-in `time_advance` uses this flag.

Any registered tool can be called invisibly. These events have no request or
result window and produce no model tokens unless the tool writes to a window.
They accept only `op`, `tool`, `arguments`, optional `source`, and optional
`duration-ticks`. Hidden tool failures are compilation errors.

For example, `chat` defaults to `metadata.agent_name` (Kidra by default). Authors
can simulate another participant with its invisible `username` argument:

```json
{"op":"invisible-tool-call", "tool":"chat",
 "arguments":{"content":"Hello!", "username":"Alex"}, "source":"user"}
```

This appends `<|msg|>Alex: Hello!<|/msg|>\n` to the already open `chat` window.
Kidra cannot supply `username` in a visible chat call.

## Showing tools

Add a **Show tools** event and select the tools to advertise using checkboxes:

```json
{"op":"show_tools", "window":"system", "tools":["chat", "file_read"],
 "randomly-add-additional-tools":true}
```

It appends formatted public schemas as excluded-loss external content to an
already open window. With the option disabled, only selected tools are shown.
With it enabled, each other tool has a deterministic 50% chance of inclusion,
seeded by trajectory UUID and event position. Selected tools always remain.
Recompiling unchanged source with the same registry gives the same selection.
This advertises capabilities; it does not restrict the execution registry.
MCP authors can insert it using `create_event`.

## Custom Python tools

Start the server or headless exporter with a folder:

```bash
python -m trajectory_remix --dataset project.sqlite --tools-dir ./custom-tools
```

Every directly contained `.py` file is loaded as one tool. Names must be unique
across custom and built-in tools. Invalid modules, duplicate names, invalid
schemas, and incompatible run signatures report a startup error. Files are
loaded from their current source bytes, avoiding stale Python bytecode caches.

For example, `custom-tools/counter.py`:

```python
name = "counter"
description = "Increment a counter."
schema = {
    "type": "object",
    "properties": {"amount": {"type": "integer"}},
    "required": ["amount"],
    "additionalProperties": False,
}

def run(args, state, windows):
    state["counter"] = state.get("counter", 0) + args["amount"]
    return str(state["counter"]), True, True
```

Custom modules are trusted Python supplied by the server operator and run with
server permissions. Trajectory text cannot select a folder or load a module.
The built-ins continue to simulate files, clocks, memory, notes, skills, and
conversations locally; they do not execute code or contact providers.

SQLite stores external tool names, full argument schemas, SHA-256 file hashes,
and per-trajectory schema dependencies. Startup compares the current files with
that catalog and recompiles affected saved trajectories. The database retains
successful compiled previews and compilation diagnostics. Missing tools and any
schema change fail compilation, even if the old arguments happen to fit the new
schema. A code-only change with the same schema recomputes state and outputs.
Restart the server to rescan files; there is no live file watcher.

To adopt a changed schema intentionally, remove the tool's references from the
trajectory and save, then add the repaired calls or `show_tools` selection. This
clears and recreates its schema dependency. Unrelated trajectories remain lazy.
Projects using the previous SQLite version are migrated additively on opening.

## Built-in simulation conventions

The built-ins share their own clock and stores inside the otherwise opaque dict.
The legacy clock/store tools increment their simulated call count and clock on
visible calls; invisible calls omit that automatic increment. The calculator,
random generator, timers, watchers and diagnostics do not implicitly advance time. Synthetic clock metadata remains
`{"synthetic_clock":{"seed":"2030-01-01T09:00:00Z","seconds_per_call":1}}`.
`time_set` accepts nullable local date/time fields and an IANA or fixed-offset
zone; null fields are deterministic from the trajectory UUID and call count.

File tools operate on virtual UTF-8 text, with immutable SHA-256 versions and
optional stale-write checks. Memory retrieval uses local lexical overlap. Notes
support task dependencies, owners, blockers, and acceptance criteria. Skills
store text only. These formats are owned entirely by the built-in tool modules.

The built-in `subagent` retains synthetic conversations. An invisible call with
`task` and invisible `response` prepares an authored response for that task.
A subsequent visible call supplies `task` and optionally `resume` and pinned
`artifacts`. Old trajectories may retain their author-only event `outcome` field;
the tool reads it through execution context. No compiler or generation branch
recognizes subagent names, and the built-in never invokes an LLM.

MCP workbenches are isolated, ephemeral state dictionaries. Their call budgets
and logs are session bookkeeping. Use executable trajectory calls for replayable
state; recording workbench observations alone does not copy its dictionary.


## Additional simulation tools

`calculator({"expression":"sin(pi/2) + log(8, 2)"})` evaluates numerical
expressions with the **simpleeval** library. Available functions include sin,
cos, tan, asin, acos, atan, atan2, sqrt, log, log10, log2, exp, degrees,
radians, floor, ceil, abs, round, min and max. Constants are pi, e and tau.
Angles use radians. Expressions are limited to 4096 characters and 256 syntax
nodes; strings, attributes, collections and non-finite results are rejected.
Evaluation retains the normal per-invocation deadline.

`random` accepts `kind: "int" | "bool" | "float" | "choice"`. Integer bounds
are inclusive (default 0–100); float bounds default to 0–1. `choice` requires a
nonempty `choices` array and can return structured JSON values. Each successful
draw advances a counter stored under `random`; the trajectory UUID and counter
seed the draw. Recompiling the same source reproduces its sequence. Failed
requests do not consume a draw. These values are for simulation, not secrets.

`timer` uses logical execution ticks without sleeping:

- `{"mode":"timeout","ticks":5}` completes five ticks after dispatch.
- `{"mode":"interval","ticks":3,"count":4}` emits at offsets 3, 6, 9 and 12,
  then completes. Intervals must be positive and counts are finite (at most 1024).
- `{"mode":"alarm","at_tick":100}` fires at or after tick 100.
- `{"mode":"alarm","timestamp":"2030-01-01T09:10:00Z","timeout_ticks":500}`
  waits for the synthetic clock to reach that timestamp. Authors must advance
  the synthetic clock explicitly; replay ticks do not represent wall seconds.

Alarms have a waiting limit of 1000 ticks by default. Intervals and timeouts are
also subject to the trajectory's existing tick limit. Timer invocations are
stateless, so idle ticks do not create state snapshots. Streamed observations
queue in firing order; the result anchor still captures the last result token.

`file_watch({"path":"/report.txt","events":["created","modified","deleted"],
"count":3,"timeout_ticks":200})` captures the current file version at dispatch
and emits subsequent matching changes until its count is met or it times out.
Defaults are all three events, one match and 1000 ticks. It polls the virtual
filesystem once per tick, comparing content hashes; it cannot observe transient
changes that are reversed between polls. It sees writes only after their
execution finishes, including delayed I/O. Calls follow stable source order: a
mutation later in that order is observed on the watcher's next tick. Timestamp
alarms follow the same rule for clock updates. Completed/timed-out watches
remove their registration from state.

`system_diagnostics` returns deterministic simulated values with
`synthetic: true`: CPU usage percentage, core count and model, RAM/VRAM total
and used bytes, GPU model, OS and architecture. Defaults describe a simulated
8-core Linux system with 16 GiB RAM and 8 GiB VRAM; they never probe the host.
The invisible `system_diagnostics_set` merges supplied fields, retaining omitted
ones, and rejects RAM/VRAM usage exceeding the configured total.

The invisible hardware configuration tool accepts, for example:

```json
{"op":"invisible-tool-call", "tool":"hardware_load_set",
 "arguments":{"file_io_ticks":5,"operation_ticks":{"file_read":2}}}
```

The six primitive file tools (read, write, patch, delete, stat and list) then
sample their delay at dispatch: two extra ticks for reads and five for the
others. Execution and mutations happen at the end of that delay. Existing
`duration-ticks` is additive. Changing configuration does not alter in-flight
calls; set an operation override to zero to remove its latency. These settings
simulate latency independently of reported CPU/RAM usage; no host load is
created. Configuration tools are invisible-only and never appear among random
`show_tools` additions.
