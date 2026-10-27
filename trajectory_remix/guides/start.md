# Start here: one lesson at a time

You are authoring or reviewing a training scenario: the user request, Kidra's
behavior and specialists' replies. Remix checks and replays it; built-in tools
simulate execution.

## 1. Read the session

Call `get_session`. Keep its `revision`, `special_tokens` and `max_window_slots`.
Only the active generation draft is read-only; unrelated samples remain editable.
Use logical window names; compilation assigns their numeric slots.

## 2. Choose your next task

For review tasks, filter before fetching trajectories. For "review five candidate
trajectories", call:

```json
{"name":"list_trajectories","arguments":{"query":"@state:candidate","limit":5}}
```

Add `@batch:<batch-id>` to the query when reviewing a particular generation batch.
Use the requested state and count in the query and `limit`; do not list the whole
dataset and check each trajectory's state manually. Results contain metadata;
fetch replay content only for the matching trajectories you will review. Read
`get_authoring_guide(topic="search")` if you need more query syntax.
For your own review, `validate_trajectory(semantic=false)` checks compiler
diagnostics without model API calls. `semantic=true` calls configured model
endpoints; it does not use the reviewing agent's subscription.

For scenario reviews, call `review_trajectory(sample_id=...)`: it automatically
fetches the checklist, source, review guide and compiler diagnostics. Use
`repair_trajectory` with the returned revision/checklist hash for targeted edits;
reread and review all rules afterward. Finish with `record_trajectory_review`
using every item in the returned schema. `#user_scenario` identifies the bundled
flow. `get_review_checklist` retrieves just the rules. These tools make no model
calls and record state 2/-2, never Ready (3).

For manual/non-scenario reviews, read `get_authoring_guide(topic="review")`, finish
repairs, reread the source and call `sign_off_trajectory` with your reviewer,
summary, verdict and reviewed `expected_revision`. Edits invalidate assessments;
compiler checks alone do not establish semantic correctness.

Read just one guide using `get_authoring_guide` with the indicated `topic`:

| Your task | Topic | Repository copy |
| --- | --- | --- |
| Review semantics or repair a generated sample | `review` | [Review checklist](review.md) |
| Learn ticks and make a tiny trajectory | `first` | [First trajectory](first.md) |
| Schedule independent specialists concurrently | `parallel` | [Parallel work](parallel.md) |
| Fix text or timing without replacing the trajectory | `edit` | [Small edits](edit.md) |
| Put virtual files into a trajectory | `sandbox` | [Sandbox setup](sandbox.md) |
| Choose which tools a beginner agent should see | `tools` | [Starter tool selection](tools.md) |
| Find trajectories by text, tags or properties | `search` | [Search syntax](search.md) |
| Inspect a complete replay with parallel tools | `example` | [Build-graph trajectory JSON](../../examples/build-graph-analysis.json) |

For new authors, finish `first`, then `parallel`.
Request `topic: "reference"` only when you need the complete manual.
`topic: "example"` returns source JSON without importing it.

## 3. Use this editing loop

Read the relevant events → make one targeted edit → inspect the result.
Pass the latest dataset `revision` as `expected_revision` on each edit. Keep the
new revision returned by that edit. A conflict means read again before retrying.
Edits are saved immediately; `save_dataset` only reports a checkpoint.

Guide objects with `name` and `arguments` are MCP calls. Add the latest
`expected_revision` to edits. Replace `$EOS` with `get_session.special_tokens.eos`.
Run example blocks individually, in order. Steps between blocks also matter.

## Keep these three boundaries clear

- A tick starts a stream. Generated text continues at one token per tick per
  window. A second start tick may still land inside the first stream.
- Independent windows can generate together. Wait for a result only when the
  next action actually depends on it.
- `create_tool_call` changes a trajectory's replay sandbox.
  `call_simulated_tool` changes a separate temporary workbench. Recording its
  transcript does not transfer its files or schedule executable replay events.
