# Tools and sandbox state

Every compilation creates a fresh Python dictionary, bootstrapped by the clock
helper with the trajectory's `tick_rate` (10 tokens/s by default). Other keys are
owned by tools. Tools own their state conventions; the compiler, training
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

## Cancellation with EOS

Generated EOS closes a tool window even while composing a request or waiting for
results ([issue #12](https://github.com/TheDudeFromCI/trajectory-remix/issues/12)).
A request ending in EOS is abandoned: its structured arguments may be incomplete,
and the tool never executes. To cancel a running call, schedule a `generate`
event containing `<|eos|>` on its tool window. Other windows continue normally.

Execution at that tick follows the normal phases: a phase-B operation or result
already completed before phase-D EOS remains committed. A still-running tool
receives one terminal cleanup invocation with
`execution_context.get().cancelled == True`. It must return promptly, clean up
its own registrations, and report `modified_state` normally. Its terminal output
is discarded and the scheduler never runs that call again, even if it ignores
cancellation. The 10-second deadline also applies to cleanup. Custom tools should
check `cancelled` before performing ordinary work. Built-in timers, watchers,
network requests, delayed file operations and subagents release pending state;
cancelled subagent conversations retain their history with status `cancelled`.

Cancellation does not undo earlier completed mutations or observations. Streaming
results stop at the EOS tick, including results whose computation already
finished. Cancelled calls have author-facing status `cancelled`; a cancelled
call's `result-anchor` captures the EOS tick so dependent events can continue.

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

The built-ins share clock anchors and stores inside the otherwise opaque dict.
Time advances with logical ticks, never with the number of tool calls. Configure
it per trajectory with `metadata.synthetic_clock`, for example
`{"seed":"2030-01-01T09:00:00Z","tick_rate":10}`. The metadata editor exposes a
**Tick rate (tokens/s)** field. Project configuration sets the default for newly
generated trajectories. Rates must be finite and positive; fractional rates work.

`time_set` and `time_advance` record `clock_unix` (Unix seconds), `clock_tick`
(the update tick), and `clock` (the same anchor formatted as ISO time). The
sandbox also contains `tick_rate`. Current time is:

```text
clock_unix + (current_tick - clock_tick) / tick_rate
```

For example, an anchor at tick 14 and a query at tick 24 at 10 tokens/s gives
one elapsed second. `time_now` reports that extrapolated time without rebasing
the anchor. `time_advance` first extrapolates to its invocation tick, then adds
its explicit seconds and records a new anchor. Before an explicit update, the
configured seed is anchored at tick zero. Named timezones retain DST behavior;
elapsed seconds are added in UTC.

Anchors stay unchanged on idle ticks, preserving sparse caches. The old
`seconds_per_call` setting is accepted when loading older projects but no longer
advances time. MCP workbenches accept `tick_rate`, track a separate current tick,
and return it in inspections and interaction logs. An instant workbench call
consumes one logical tick; long-running calls consume their execution ticks.
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

- `{"mode":"timeout","seconds":0.5}` completes after 0.5 simulated seconds
  (five ticks at the default rate).
- `{"mode":"interval","seconds":0.3,"count":4}` emits at 0.3-second intervals,
  then completes. Intervals must be positive and counts are finite (at most 1024).
- `{"mode":"alarm","at_tick":100}` fires at or after tick 100.
- `{"mode":"alarm","timestamp":"2030-01-01T09:10:00Z","timeout_seconds":50}`
  waits for the tick-derived synthetic clock to reach that timestamp. Clock
  updates can also jump it forwards or backwards.

Alarms have a waiting limit of 100 simulated seconds by default. `seconds` and
`timeout_seconds` follow the same anchored clock, including explicit clock
advancement. Intervals and timeouts are also subject to the trajectory tick
limit. Timer state records the start time and firing count; idle ticks do not
create snapshots. After a large clock jump, an interval catches up one firing
per tick. Explicit `ticks` and `timeout_ticks` remain available as alternatives
for schedules defined in logical ticks. Streamed observations
queue in firing order; the result anchor still captures the last result token.

`file_watch({"path":"/report.txt","events":["created","modified","deleted"],
"count":3,"timeout_seconds":20})` captures the current file version at dispatch
and emits subsequent matching changes until its count is met or it times out.
Defaults are all three events, one match and 100 simulated seconds. Explicit
`timeout_ticks` remains an alternative. It polls the virtual
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

The ten file operations (read, write, patch, delete, stat, list, copy, move,
rename and search) then
sample their delay at dispatch: two extra ticks for reads and five for the
others. Execution and mutations happen at the end of that delay. Existing
`duration-ticks` is additive. Changing configuration does not alter in-flight
calls; set an operation override to zero to remove its latency. These settings
simulate latency independently of reported CPU/RAM usage; no host load is
created. Configuration tools are invisible-only and never appear among random
`show_tools` additions.

## File operations

All paths are virtual POSIX paths; files never touch the host filesystem. Paths
are normalized with a leading slash, and traversal/backslashes/NUL are rejected.
The store contains text files; directories are implicit path prefixes.

- `file_read` reads the whole current file by default. Optional `start_line` and
  `end_line` are inclusive, 1-based ranges; `line_numbers: true` prefixes each
  returned line. Ranged results include `total_lines`. Ranges past EOF are empty;
  an explicitly reversed range fails. The same options work with historical
  `version` reads. Newline characters are preserved.
- `file_patch` already supports exact text replacement with `path`, `old`, `new`.
  It also accepts `edits: [{"old":"...","new":"..."}, ...]` instead of old/new.
  Edits apply in order to a working copy and commit atomically only if every old
  text matches exactly once. Optional `expected_version` prevents stale edits.
  `file_write` remains available for full replacement.
- `file_copy`, `file_move`, and `file_rename` take `source` and `destination`.
  Existing destinations fail unless `overwrite: true`; optional
  `expected_version` checks the source. Move and rename have the same atomic
  behavior. Historical versions at both paths remain available after transfers.
- `file_list` optionally filters sorted paths with `glob`, such as `/src/*.py`.
  `file_search` requires `query`, treats it as literal text by default, and
  optionally accepts `regex`, `case_sensitive` (default true), `glob`,
  `context_lines` (0–20), and `max_results` (default 100, maximum 1000). Matches
  include path, 1-based line number, line text and surrounding lines, with a
  `truncated` indicator. Matching is per line. Globs are case sensitive and `*`
  can span path separators. Regex evaluation has the standard invocation timeout.

## Simulated permissions and I/O failures

The invisible `tool_permissions_set` replaces the current permission policy:

```json
{"op":"invisible-tool-call", "tool":"tool_permissions_set", "arguments":{
  "default":"allow", "rules":[
    {"tool":"file_write", "path":"/reports/*", "decision":"deny",
     "remaining":1, "message":"User clicked Reject"}
  ]}}
```

The first matching rule wins for each target. `tool` and `path` are case-sensitive
globs, defaulting to any tool/target when omitted. `path` matches normalized
`path`, `source`, `destination`, or an HTTP `url`; transfers need access to both
endpoints. `remaining` limits the number of dispatches using a rule; omitted
means persistent. Defaults allow access; `default: "deny"` creates an allowlist.
Rejection returns a normal `Permission denied` tool error before execution,
without file writes, requests, random draws or other tool side effects. Policy
consumption itself is cached. Rules apply to newly dispatched calls; use EOS to
stop a running call. Invisible author calls bypass permissions so setup and
policy repair remain possible. File list/search omit paths denied for their
respective tool names without consuming per-file rules. These are training
simulation controls, not an isolation boundary for trusted custom Python code.

The invisible `io_failure_set` replaces deterministic fault rules:

```json
{"op":"invisible-tool-call", "tool":"io_failure_set", "arguments":{"rules":[
  {"tool":"file_*", "path":"/data/*", "code":"EIO", "remaining":1,
   "message":"Disk temporarily unavailable"}
]}}
```

Fault matching uses the same tool/path globs. The first matching rule is sampled
once at dispatch, after permission checks and before I/O side effects or latency.
It applies to all ten file operations, `file_watch` and `network_request`,
including invisible calls. Codes such as `EIO`, `ENOSPC`, `EACCES`, `ETIMEDOUT`, or
`ECONNRESET` are author-selected labels. Omit `remaining` to keep failing, or
use one failure then let retries succeed. `{"rules":[]}` clears faults. Failed
operations leave files intact and do not consume network fixture responses.

## Simulated network I/O

`network_request` models HTTP with `url`, optional `method` (default GET),
`headers`, `body`, and `timeout_seconds` (default 30 simulated seconds). It never
uses host networking. Authors configure deterministic routes invisibly:

```json
{"op":"invisible-tool-call", "tool":"network_set", "arguments":{"routes":[
  {"url":"https://example.test/api", "method":"POST", "request_body":"hello",
   "responses":[
     {"status":503, "body":"Try again", "latency_seconds":0.5},
     {"status":200, "headers":{"Content-Type":"text/plain"}, "body":"Saved"}
   ]}
]}}
```

Routes match in order by method and URL glob, with optional exact request body
and a subset of request headers (case-insensitive header names). Each dispatch
consumes the next response; the last response repeats. Route replacement resets
cursors while in-flight requests retain their sampled response. Unmatched URLs
fail explicitly. Request URLs require HTTP/HTTPS, a host and no credentials.

Responses contain status (default 200), headers and text body. HTTP errors such
as 404/429/503 are successful transport results, letting Kidra interpret them.
Optional `error` simulates `dns`, `connection_refused`, `connection_reset`, `tls`
or `timeout`. Optional `latency_seconds` uses the same anchored tick clock as
timers; clock jumps affect waiting requests. Transport failures and timeouts
return tool errors; explicit fixture timeouts wait until the request deadline.
At an exact latency/deadline tie the response wins. HEAD returns an empty body.
There are no automatic retries or redirects. Fixtures and pending responses are
bounded by the workbench byte budget. Idle requests do not add state snapshots.
EOS cancellation releases pending responses; the dispatched fixture remains
consumed. Use response sequences to teach retries, rate limiting and recovery.
