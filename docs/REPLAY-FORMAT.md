# From the old replay format

Earlier versions of Remix described trajectories as a **replay**: events keyed by tick numbers
and anchors, with an executable sandbox for tools. That format is retired and **not
supported**. There is no reader and no migration. Opening an old project fails with a clear
message, and imports skip old records.

If you have historical data, keep the old project database separately. To bring its content
across, re-author it in the graph format.

## How the ideas map

| Old replay | Now |
| --- | --- |
| Events at tick keys such as `"12"` or `{N_TICK:4}+1` | Events with `after` dependencies. There are no authored ticks. |
| `open` events and window slots | Windows exist from the start. Slots and headers are derived at compile time. |
| `generate` and `append` | `agent_text` and `external_text` events. |
| Executable tools and invisible tool calls | Authored `tool_call` and `tool_response` events. Nothing runs. |
| Simulated clock | Removed. |
| NOOP waits | Derived by the compiler when a window is idle. |

Timing still exists, but only in compiled output. The [compiler](GRAPH-COMPILER.md) schedules
the graph into ticks when you preview with **Tick history** or export.

Start with the [source format](GRAPH-SOURCE.md).
