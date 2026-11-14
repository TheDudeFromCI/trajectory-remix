# Sandbox and custom tools

Every trajectory runs against a **sandbox**: a Python dictionary that tools read and
write while the trajectory is compiled. This page explains how tools execute, how
they interact with windows, and how to write your own. For what each built-in tool
does, see the [Tools reference](TOOLS.md).

New to this? The [sandbox lesson](../trajectory_remix/guides/sandbox.md)
(`get_authoring_guide(topic="sandbox")`) is a short hands-on exercise.

## The model

- Each compilation starts from a fresh dictionary that holds only `tick_rate`
  (default 10 tokens per second). Tools add whatever keys they need, and nothing else
  interprets them.
- Kidra (the model) sees **window text and tool output only**. Sandbox state is for
  authors, shown in the dashboard's **Sandbox** panel. Training exports contain only
  replay rows and window lifecycles.
- Recompiling throws the state away and recomputes it, so results are reproducible.
- Frames report `sandbox` and `tool_calls` separately, so a tool may use any key,
  including `tool_calls`, without clashing with the scheduler.

## Custom Python tools

A tool is a Python module defining `name`, a JSON-schema `schema`, and a `run`
function:

```python
def run(args: dict, state: dict, windows: list[WindowProperties]) -> tuple[str, bool, bool]:
    return output, is_done, modified_state
```

| Return value | Meaning |
| --- | --- |
| `output` | Text for the model. A nonempty output becomes a masked observation at this tick. |
| `is_done` | `True` when the call is finished. |
| `modified_state` | `True` if you changed `state`. Report every mutation. |

`run` is called once per tick, in stable source order, until `is_done` is true.
Instant tools finish on their first call. A long-running tool can keep its progress
in `state` and emit output over many ticks. Execution starts the tick after the
request's last token (invisible calls start at their scheduled tick).
`delivery: "stream"` streams observations at `tokens-per-tick`, and `result-anchor`
captures the final delivered token.

### Example

```python
# custom-tools/counter.py
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

Two more examples, a visible `spell` tool and an invisible-only `potato_mode`, are in
[`examples/custom_tools`](../examples/custom_tools/README.md).

### Loading custom tools

```bash
python -m trajectory_remix --dataset project.sqlite --tools-dir ./custom-tools
```

Every `.py` file directly in that folder becomes one tool. Names must be unique
across custom and built-in tools. Invalid modules, duplicate names, bad schemas and
wrong signatures fail at startup. Restart to pick up changes; there is no file
watcher. Trajectory text cannot choose a folder or load a module.

Custom tools are **trusted Python** that runs with server permissions. The built-ins
are simulations and never run code, probe the host or contact providers.

### Rules for tool code

- **State.** Return `modified_state=True` whenever you change `state`. The compiler
  then stores a snapshot for that tick, so sparse snapshots cover the quiet ticks in
  between. Scrubbing in the dashboard picks the right snapshot and never re-runs
  tools. State must be deep-copyable, and JSON-compatible if you want it shown in the
  dashboard or API. Module globals are **not** kept between calls.
- **Time limit.** Each call has a 10-second wall-clock deadline and fails with an
  error naming the tool and tick if exceeded. A tool may run for many ticks as long
  as each call returns promptly (trajectories are capped at 100,000 ticks). Custom
  tools run in a disposable worker process that is terminated on timeout, and the
  state and window writes of a failed call are discarded.
- **Execution context.** `trajectory_remix.tools.execution_context.get()` gives the
  current tick, start tick, call ID, author metadata, and `cancelled`. It is optional.
- **Legacy delay.** `duration-ticks` is a compatibility delay that built-ins still
  honor. New tools should do their own progress inside `run`.

### Windows

`windows` lists the currently open windows. Each has read-only `name` and `purpose`
and a writable text `content` (dictionary access works too). A tool can read other
windows, append, or replace content. Writes are masked external input in phase B.
A replacement removes the old text from later frames and attention while keeping
history, and the automatic BOS and header stay. Text generated in the same tick is
visible on the next tick. Window changes and sandbox changes are independent, so a
tool that only edits a window returns `modified_state=False`.

### Schema changes and recompilation

SQLite records each external tool's name, full schema, file hash and the
trajectories that depend on it. On startup Remix compares the files with that record
and recompiles affected trajectories:

- A missing tool or **any schema change** fails compilation, even if old arguments
  would still fit.
- A code-only change with the same schema recomputes state and output.
- To adopt a changed schema, remove the tool's calls (or its `show_tools` entry) from
  the trajectory, save, then add the repaired ones. Unrelated trajectories are not
  touched until opened.

## Cancelling with EOS

A generated EOS on a tool window ends it, even mid-request or mid-run.

- A request that ends in EOS is abandoned, and the tool never runs.
- To cancel a running call, schedule a `generate` event containing the EOS token on
  its tool window. Other windows carry on.
- Anything already committed before the phase-D EOS stays. A running tool gets one
  last call with `execution_context.get().cancelled == True` to clean up and report
  state, within the same 10-second deadline. Its output is dropped and it is never
  called again. Well-behaved tools check `cancelled` first.
- Built-in timers, watches, web calls, delayed file operations and subagents release
  their pending state. Cancelled subagent conversations keep their history with
  status `cancelled`.
- Earlier completed mutations are **not** undone. Streaming results stop at the EOS
  tick. The call's author-facing status is `cancelled`, and its `result-anchor`
  captures the EOS tick so dependent events can continue.

## Invisible arguments and external activity

Some activity should happen in the world without the model seeing a request:

- **Invisible arguments.** Mark a schema property `"invisible": true` (or
  `"x-invisible": true`). Public schemas and `show_tools` omit it, and a visible call
  that supplies it is rejected, including through MCP. Authors still see it in the
  editor and in `list_simulated_tools`.
- **Invisible tools.** A module-level `invisible = True` restricts the whole tool to
  `invisible-tool-call` events. It is hidden from model tool lists, the visible-call
  editor, `show_tools` and random extras, and listing it in `show_tools` is an error.
  The built-in `time_advance` works this way.
- **Invisible calls.** Any registered tool can be called invisibly. These events have
  no window and produce no tokens unless the tool writes to a window. They accept only
  `op`, `tool`, `arguments`, `source` and `duration-ticks`, and a failure is a
  compilation error.

Example: simulate a message from a user. `chat` normally speaks as the agent
(`metadata.agent_name`, default Kidra), and its invisible `username` argument lets an
author pick another speaker:

```json
{"op":"invisible-tool-call", "tool":"chat",
 "arguments":{"content":"Hello!", "username":"Alex"}, "source":"user"}
```

This appends `<|msg|>Alex: Hello!<|/msg|>\n` to the open `chat` window.

## Showing tools to the model

Open a window with `window-type: "tools"`, then add a `show_tools` event:

```json
{"op":"show_tools", "window":"tools", "tools":["chat", "file_read"],
 "randomly-add-additional-tools":true}
```

It appends the selected public schemas to that window as masked external text. With
the random option on, every other tool has a deterministic 50% chance of being
included, seeded by trajectory UUID and event position, and your selections are always
kept. The same source and registry give the same result. This only **advertises**
tools; it does not restrict what can run. Keep schemas out of the system window, or
you get a `tools_in_system` warning.

## Setting synthetic time

Time advances with ticks, never with the number of calls or real sleep. Configure it
per trajectory with `metadata.synthetic_clock`:

```json
{"seed": "2030-01-01T09:00:00Z", "tick_rate": 10}
```

The metadata editor has a **Tick rate (tokens/s)** field, and project settings supply
the default for generated trajectories. Rates must be finite and positive, and
fractions are fine.

`time_set` and `time_advance` write an anchor: `clock_unix`, `clock_tick` and an ISO
`clock`. The current time is:

```text
clock_unix + (current_tick - clock_tick) / tick_rate
```

An anchor at tick 14 queried at tick 24 with 10 tokens/s is one second later.
`time_now` extrapolates without moving the anchor. `time_advance` extrapolates to its
tick, then adds its seconds and records a new anchor. Before any update the seed is
anchored at tick 0. Named time zones keep daylight-saving behavior.

`time_set` takes nullable local date and time fields plus an IANA or fixed-offset
zone, and all seven keys are required. Nulls are filled deterministically from the
trajectory UUID and call count. Anchors stay put on idle ticks, which keeps snapshots
sparse. The old `seconds_per_call` setting loads from old projects but has no effect.

MCP workbenches take `tick_rate`, keep their own current tick and report it in logs.
They are isolated, ephemeral dictionaries, so record an interaction into a trajectory
(or use executable trajectory calls) to keep it. See the
[MCP authoring reference](MCP-AUTHORING.md#simulated-tool-workbenches).

## Built-in conventions

- **Files** are virtual UTF-8 text with immutable SHA-256 versions and optional
  stale-write checks.
- **Memory** search uses lexical overlap, not embeddings.
- **Notes** are prioritized TODO tasks with blockers. Using any note tool makes the
  compiler open a managed, read-only `notes` window at tick 0. It holds all six
  priority buckets as JSON, is replaced in phase B whenever a task changes (with the
  old text dropped from attention), uses a normal window slot, and cannot be authored.
- **Skills** store text only.
- **Subagent** keeps synthetic conversations whose replies the author writes. It never
  calls a model. An invisible `subagent` call with `task` and invisible `response`
  prepares the reply for a later visible call. Older trajectories may carry an
  author-only `outcome` field, which the tool reads.

Full argument lists, timing and examples for every tool are in the
[Tools reference](TOOLS.md).
