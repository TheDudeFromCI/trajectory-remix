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
Visible built-in calls increment their simulated call count and clock; invisible
calls omit that automatic increment. Synthetic clock metadata remains
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
