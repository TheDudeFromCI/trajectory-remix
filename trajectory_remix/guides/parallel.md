# Parallel work: start independent tasks together

Goal: author two independent specialist turns, then follow up with one specialist
as soon as its result arrives. You write the replies; no real agents run.
Call `get_session`; add the latest `expected_revision` to edits below.

## 1. Identify dependencies before assigning ticks

The scenario: a user wants a proposal for a `double(value)` API and a short
release note. The API specialist can propose a contract while the writing
specialist drafts a note from the requirement. Clarifying accepted input types
depends on the API answer, but does not depend on the writing answer.

```text
user request ─┬─ API proposal ── clarify input types
              └─ release note
```

Authoring MCP edits happen one after another so revisions stay current. Their
scheduled replay work can still overlap. Do not use the end of the whole
trajectory as the start tick for every new event.

## 2. Create the draft and request

```json
{
  "name": "create_trajectory",
  "arguments": {
    "sample_id": "parallel-start"
  }
}
```

```json
{
  "name": "create_event",
  "arguments": {
    "sample_id": "parallel-start",
    "tick": "0",
    "event": {
      "op": "open",
      "window": "request",
      "window-type": "document"
    }
  }
}
```

```json
{
  "name": "create_event",
  "arguments": {
    "sample_id": "parallel-start",
    "tick": "0",
    "event": {
      "op": "append",
      "window": "request",
      "source": "user",
      "content": "Propose a double(value) API and draft a release note. Clarify the accepted input types.",
      "loss": {
        "mask": "exclude"
      }
    }
  }
}
```

## 3. Schedule both independent requests at tick 10

```json
{
  "name": "create_tool_call",
  "arguments": {
    "sample_id": "parallel-start",
    "tick": "10",
    "tool": "subagent",
    "window": "api-call",
    "arguments": {
      "task": "Propose a double(value) API that returns twice its input. No implementation is needed."
    },
    "duration_ticks": 600,
    "auto_anchor": true,
    "outcome": {
      "ok": true,
      "result": {
        "conversationId": "api-specialist",
        "response": "Proposed double(value), returning value * 2. The contract should explicitly require numeric inputs."
      }
    }
  }
}
```

```json
{
  "name": "create_tool_call",
  "arguments": {
    "sample_id": "parallel-start",
    "tick": "10",
    "tool": "subagent",
    "window": "writing-call",
    "arguments": {
      "task": "Draft one release-note sentence for a proposed double(value) API that returns twice its input. Describe it as proposed, not implemented."
    },
    "duration_ticks": 900,
    "auto_anchor": true,
    "outcome": {
      "ok": true,
      "result": {
        "conversationId": "writing-specialist",
        "response": "Proposed: double(value) returns twice the supplied numeric value."
      }
    }
  }
}
```

Each call creates its own tool window automatically, open from tick 0. Requests
generate at one token per tick; the tool dispatches on the next tick after the
last request token. `duration_ticks` measures execution from dispatch. Results
are inserted at completion and masked from training loss. Do not manually add
duplicate requests or results. `outcome` is author-only data, outside `arguments`.

## 4. Verify overlap before continuing

Keep each response's `tool_call.start`, `end`, `result_end` and `result_anchor`.
Check `max(api.start, writing.start) < min(api.end, writing.end)`. If false,
lengthen the earlier-finishing call with `update_event` (`duration-ticks` in
`settings`), then inspect the resolved calls again with `get_sandbox_state`.
Different request lengths mean equal request ticks need not mean equal dispatches.

Call `get_sandbox_state` at `max(api.start, writing.start)`. Expect both
conversations to be `running`, with submitted tasks but no future assistant
answers. This author-side inspection adds no model observation. If Kidra needs
status in the scenario, schedule a visible `runtime_status` call while both run.

## 5. Follow up on the API result without waiting for unrelated work

In this fresh draft the API call gets result anchor 1. Check the returned
`result_anchor`; substitute it below if different. The anchor marks the final
result token, including streamed delivery if enabled later.

```json
{
  "name": "create_tool_call",
  "arguments": {
    "sample_id": "parallel-start",
    "tick": "{N_TICK:1}+1",
    "tool": "subagent",
    "window": "api-followup",
    "arguments": {
      "task": "Clarify which numeric input types your proposed contract accepts.",
      "resume": "api-specialist"
    },
    "duration_ticks": 20,
    "outcome": {
      "ok": true,
      "result": {
        "conversationId": "api-specialist",
        "response": "The proposed contract accepts int and float inputs; other types are outside its scope."
      }
    }
  }
}
```

`resume` continues the retained conversation. A window name does not identify a
conversation. Use separate windows for overlapping token streams; numeric tick
differences alone do not prevent collisions in one window.

## 6. Inspect and finish the scenario

Validate. Use `get_sandbox_state` at the follow-up's `start` to check that the API
conversation is running its second turn while the writing conversation still
runs its first. If request tokenization changes that relation, adjust durations
and inspect again. At the follow-up's completion, expect four messages in its
retained history: user, assistant, user, assistant.

For a final answer using both results, read `get_anchors` and start after the
later required result (also include the follow-up's `result_end`). Open a `chat`
window, generate an answer describing the proposal, and close windows with
generated EOS after their information has been consumed. These three calls
teach timing; they are not by themselves a finished user-facing conversation.

Next: `topic="edit"` for local repairs, or `topic="sandbox"` for virtual files.
