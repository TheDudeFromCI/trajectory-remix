# Start here: one lesson at a time

You are authoring a training scenario. You write the user request, Kidra's
behavior and the specialists' replies. Remix checks and replays that scenario;
it does not run code or launch real specialists.

## 1. Read the session

Call `get_session`. Keep its `revision`, `special_tokens` and `max_window_slots`.
If generation is running, stop editing until it is stopped in the dashboard.
Use logical window names; compilation assigns their numeric slots.

## 2. Choose your next task

Read just one guide using `get_authoring_guide` with the indicated `topic`:

| Your task | Topic | Repository copy |
| --- | --- | --- |
| Learn ticks and make a tiny trajectory | `first` | [First trajectory](first.md) |
| Schedule independent specialists concurrently | `parallel` | [Parallel work](parallel.md) |
| Fix text or timing without replacing the trajectory | `edit` | [Small edits](edit.md) |
| Put virtual files into a trajectory | `sandbox` | [Sandbox setup](sandbox.md) |
| Choose which tools a beginner agent should see | `tools` | [Starter tool selection](tools.md) |

For a first session, finish `first`, then `parallel`. The tiny greeting in `first`
teaches event syntax; the parallel lesson teaches the intended coordination behavior.
Request `topic: "reference"` only when you need the complete manual.
For a complete example rather than a minimal guided exercise, call
`get_example_trajectory` with no arguments. Its `sample` field contains
the entire build-graph replay, including parallel file tools and
invisible setup. This read does not import the sample or alter revisions.
See [the example in the repository](../../examples/build-graph-analysis.json).

## 3. Use this editing loop

Read the relevant events → make one targeted edit → inspect the result.
Pass the latest dataset `revision` as `expected_revision` on each edit. Keep the
new revision returned by that edit. A conflict means read again before retrying.
Edits are saved immediately; `save_dataset` only reports a checkpoint.

The guides show JSON objects with `name` and `arguments`. These mean MCP tool
calls, not trajectory JSON to paste into the source editor. Examples omit
`expected_revision` to stay readable; add the latest revision to each edit's
`arguments`. Replace `$EOS` in example content with `get_session.special_tokens.eos`.
Run example blocks individually, in order. Steps between blocks also matter.

## Keep these three boundaries clear

- A tick starts a stream. Generated text continues at one token per tick per
  window. A second start tick may still land inside the first stream.
- Independent windows can generate together. Wait for a result only when the
  next action actually depends on it.
- `create_tool_call` changes a trajectory's replay sandbox.
  `call_simulated_tool` changes a separate temporary workbench. Recording its
  transcript does not transfer its files or schedule executable replay events.
