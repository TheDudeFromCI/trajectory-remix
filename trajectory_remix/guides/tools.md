# Starter tool selection for smaller agents

This is a recommendation for whoever configures the MCP client or agent's tool
allowlist. Remix currently exposes its full tool catalog; there is no built-in
starter-profile flag. If your client supports tool selection, expose only the
current lesson's set. Otherwise put the list in the agent's instructions and
understand that this is guidance, not enforced filtering.

## 1. Begin with ordinary events and inspection

Expose these tools for the `first` lesson:

```text
get_session
get_authoring_guide
create_trajectory
create_event
validate_trajectory
get_anchors
get_window_history
preview_snapshots
```

This set is enough to create, validate and inspect a tiny example. Keep
`create_trajectory` to its empty-template form in the prompt; its full `sample`
argument is an import feature, not needed for this lesson. Tool-name filtering
does not restrict arguments.

## 2. Add local repair tools when editing begins

```text
list_trajectories
list_events
get_event
patch_event_content
move_event
update_event
delete_event
```

Use `list_trajectories` to locate an existing sample, then read only the events
you need. Require current revisions and relisting after structural edits.
Teach the `edit` lesson before asking for a large finished example.

## 3. Add scheduled tools for parallel work and sandbox state

```text
list_simulated_tools
create_tool_call
get_sandbox_state
```

`list_simulated_tools` discovers schemas; it does not execute a workbench call.
Keep this distinction explicit in the agent prompt. For `parallel`, begin with
the synthetic `subagent` tool and authored outcomes. For `sandbox`, add
`file_write` and `file_read` through `create_tool_call`. Introduce other synthetic
tools only as the scenario needs them. They are names inside `create_tool_call`,
not additional top-level MCP tools selected by the client allowlist.

## 4. Defer the rest until a concrete need appears

| Defer initially | Introduce when |
| --- | --- |
| `apply_batch` | The agent can make and validate individual edits; an atomic multi-edit repair is needed. |
| `get_trajectory`, `replace_trajectory`, `create_trajectory.sample` | Full-source inspection, import or a structural migration is specifically needed. Prefer event reads for routine edits. |
| `create_tool_session`, `list_tool_sessions`, `get_tool_session`, `call_simulated_tool`, `record_tool_interaction`, `reset_tool_session`, `delete_tool_session` | The user explicitly needs a separate ephemeral workbench and understands that recording does not transfer state. |
| `duplicate_trajectory`, `duplicate_event`, `rename_window`, `delete_trajectory` | Copying, renaming or removal is part of the task. |
| `get_edit_history`, `undo_edits`, `redo_edits`, `diff_trajectories` | The agent needs source comparison or recovery and understands dataset-wide history. |
| Metadata editing, metadata queries/counts, semantic validators | Curation or evaluation requires them. |

`save_dataset` is optional: source edits already commit immediately.
Leave advanced options such as custom request segments, streamed results and
loss weights until the basic timing exercise works. Use `auto_anchor` early:
it avoids guessing token durations. Use invisible mutations when external
state must exist; do not substitute workbenches just because they look simpler.

## Suggested short agent prompt

> Read get_session and get_authoring_guide. Request only the next needed topic.
> Make small event edits with the latest expected_revision. Schedule independent
> tasks in separate windows with overlapping execution intervals; wait only for
> real dependencies. Inspect resolved timing and sandbox state before claiming
> success. Use create_tool_call for trajectory tools. Workbenches are separate
> temporary state. You author synthetic specialist replies; no code or agents run.

Success at each stage is observable: a valid first trajectory; a local edit that
preserves anchors; two specialists running together; a seeded file read from the
trajectory. Expand the available tools after those checks, rather than attaching
the full manual to every prompt.
