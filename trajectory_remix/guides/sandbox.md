# Sandbox setup: put files in the trajectory

Goal: create a virtual source file that later replay calls can read. These are
text files in a simulation; saving Python text does not execute Python.
Call `get_session` and pass the latest `expected_revision` on each edit.

## 1. Select the correct kind of state

| Goal | Use | Identifies state with |
| --- | --- | --- |
| Change files during trajectory replay | `create_tool_call` | `sample_id` and `tick` |
| Inspect those files at a replay tick | `get_sandbox_state` | `sample_id` and integer `tick` |
| Experiment in an ephemeral RAM workbench | `call_simulated_tool` | workbench `session_id` |

A workbench is separate from every trajectory and from the MCP transport session.
`record_sample_id` or `record_tool_interaction` records request/result **text**;
it does not transfer workbench files or create executable replay events.
Do not prepare a trajectory's codebase using workbench calls.

## 2. Create a draft and seed one file as external activity

```json
{
  "name": "create_trajectory",
  "arguments": {
    "sample_id": "sandbox-start"
  }
}
```

```json
{
  "name": "create_tool_call",
  "arguments": {
    "sample_id": "sandbox-start",
    "tick": "0",
    "tool": "file_write",
    "arguments": {
      "path": "/math.py",
      "content": "def double(value):\n    return value * 2\n"
    },
    "invisible": true,
    "source": "initial-fixture"
  }
}
```

This author-supplied fixture commits at tick 0 without model tokens, a window or
an observation. `source` attributes the scenario actor. Do not add `window`,
`outcome` or delivery options to invisible calls. For a specialist's later edit,
schedule the mutation at the intended publication tick and attribute that actor.
A subagent reply claiming it wrote a file does not itself create the file.

## 3. Verify state, then schedule a visible read

```json
{
  "name": "get_sandbox_state",
  "arguments": {
    "sample_id": "sandbox-start",
    "tick": 0
  }
}
```

Expect `sandbox.files["/math.py"]` to contain the function. This inspection is
for the author and gives Kidra no observation. To make the content visible:

```json
{
  "name": "create_tool_call",
  "arguments": {
    "sample_id": "sandbox-start",
    "tick": "1",
    "tool": "file_read",
    "window": "read-math",
    "arguments": {
      "path": "/math.py"
    },
    "auto_anchor": true
  }
}
```

This generates the request, executes the read and supplies a masked result in
`read-math`. Keep its returned `tool_call.end` and `result_anchor`. Inspect state
at `end` and check that the read succeeded. A subsequent reasoning stream that
uses the contents starts after the result anchor, not just after request tick 1.
Validate the draft. Add a user request, coordination and final response before
treating this sandbox exercise as a complete training example.

## 4. Keep setup and observed behavior distinct

Use invisible mutations for externally supplied initial files or specialist
changes. Use visible calls when Kidra itself should request an operation and
observe its result. Invisible data is not automatically known by Kidra.
File reads, version references and specialist artifact sharing are separate
steps; request `topic="reference"` when you need version-pinned reviews.

If earlier work exists only in a workbench, inspect that live session and extract
its needed final file contents, then author trajectory mutations for them. Check
the resulting trajectory state with `get_sandbox_state`. Workbenches are RAM-only;
reset, deletion or a server restart loses their state.
