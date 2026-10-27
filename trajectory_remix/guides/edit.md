# Small edits: read, change one event, check

Goal: fix wording or timing without replacing a trajectory's JSON. This example
continues `first-steps` from [First trajectory](first.md). For another sample,
substitute its ID and the addresses returned by `list_events`.

For semantic review or repairs to generated samples, first read
`get_authoring_guide(topic="review")`. Fixing compiler warnings alone is insufficient.

## 1. Find the current address and revision

```json
{
  "name": "list_events",
  "arguments": {
    "sample_id": "first-steps",
    "window": "chat"
  }
}
```

Find the `generate` event. An address is its exact **string** `tick` key and its
zero-based `event_index` within that key, not its resolved numeric start tick.
For the unchanged first lesson these are `"{N_TICK:1}+1"` and `0`.
Read `get_event` if you need its complete content. Use the latest returned
`revision` as `expected_revision` on the next edit.

## 2. Change just the answer's content

Replace `$EOS` below with the configured EOS spelling. Preserve the existing
anchor, since later window closures depend on it.

```json
{
  "name": "patch_event_content",
  "arguments": {
    "sample_id": "first-steps",
    "tick": "{N_TICK:1}+1",
    "event_index": 0,
    "content": "Hello there!{N_TICK:2}$EOS",
    "validate": true
  }
}
```

This changes one event's content and keeps its other settings. It does not
rewrite the source sample. Validation rejects the edit if it breaks compilation.

## 3. Move its start without detaching the dependency

```json
{
  "name": "move_event",
  "arguments": {
    "sample_id": "first-steps",
    "tick": "{N_TICK:1}+1",
    "event_index": 0,
    "tick_offset": 2,
    "validate": true
  }
}
```

The new key is `"{N_TICK:1}+3"`: still relative to the reasoning completion.
`tick_offset` changes an anchor offset without freezing its current numeric tick.
The answer's end mark and the dependent closures move with the longer answer.

## 4. Relist, validate and inspect

Call `list_events` again after moving, inserting or deleting events: addresses
may have changed. Validate and inspect `get_window_history(window="chat")`.
Expect the revised greeting and a start after mark 1. Structural validity alone
does not prove the dialogue makes sense; read the changed text too.

## Pick the smallest applicable edit

| Change | Tool |
| --- | --- |
| Wording or segments | `patch_event_content` |
| Start tick, anchor offset or ordering | `move_event` |
| Loss, window or tool arguments/duration | `update_event` |
| Add one ordinary event | `create_event` |
| Add an executable tool interaction | `create_tool_call` |
| Remove one event | `delete_event` |

Use source field spellings inside `update_event.settings`, such as
`"duration-ticks"`; top-level `create_tool_call` uses `duration_ticks`.
`update_event` replaces supplied field values, so read existing `arguments`
before replacing that object. For executable calls, update structured `tool`,
`arguments` or authored `outcome`; changing request wording alone does not change
what executes. Result text is computed from those fields.

On a revision conflict, reread events and reapply the intended small change.
On a compile error, inspect its diagnostic path and repair that event. Avoid
`replace_trajectory` and `apply_batch` while learning these steps.

Edits invalidate prior reviews: automated samples return to Pending, human drafts
to Unfinished. If the user asked you to repair **and validate**, reread and review
the repaired trajectory, then use `sign_off_trajectory` with your reviewer label,
summary, verdict (`validated` or `rejected`), and the reviewed `expected_revision`.
It records state 2 or -2 without model API calls; it never grants Ready (3).
For scenario-tagged samples use `review_trajectory` to automatically fetch the
checklist, `repair_trajectory` for atomic targeted edits, then reread and use
`record_trajectory_review` with every per-rule result. See `topic="review"`.

If streams overlap in one window, inspect their full start/end intervals.
For a dependent stream, move it after the earlier completion anchor. For
independent work, use separate windows and keep the overlap. Moving a start
from tick 10 to tick 11 alone does not make a long stream finish first.
