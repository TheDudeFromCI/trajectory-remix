# Dashboard guide

The dashboard is a browser app served at `http://127.0.0.1:8765`. This page walks
through the normal workflow. For the meaning of the source JSON, see the
[Replay format](REPLAY-FORMAT.md).

## Basic workflow

1. **Import** with **File → Import**. You can pick several `.json`, `.jsonl` and
   `.kidra-dataset` files at once. JSON may hold one trajectory, an array, or an
   object with a `samples` array; JSONL holds one trajectory per line. Imports
   append to the project and are all-or-nothing: malformed JSON or a duplicate sample
   ID rejects the whole batch. Structurally invalid trajectories still import, stay
   editable, and show diagnostics when compiled.
2. **Replay** the trajectory to see the window state at any tick.
3. **Edit** events, or edit the Source JSON directly.
4. **Mark Ready** (**Edit → Set State → Ready**) after review, so the trajectory is
   included in training exports.
5. **Export** with **File → Export → Current Trajectory** (source JSON), or use the
   [token export](EXPORT.md) for training.

Edits are saved automatically. Browser drafts are committed after a short compile
delay, and applied edits and accepted generated trajectories are saved at once.

## Undo and redo

**Edit → Undo / Redo** reverses source edits made from the dashboard, MCP or
generation. Outside text fields, use Ctrl/Cmd+Z, and Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y
to redo; focused text editors keep their own undo.

## Replay

The replay pane shows the window state **after each tick commits**.

- Step forward and back, scrub, play, or jump to a tick.
- **Space** toggles playback, and the arrow keys step when no editor has focus.
- Closed windows stay visible for inspection, even though their content is no
  longer available to later model decisions.

## Timeline

The timeline shows each window's lifetime and what happened in it:

- Generated output is colored by window, and external input is violet.
- Pale bars are idle or open intervals, and a vertical end cap marks EOS.
- Tool calls get their own duration bars. Hover to see arguments and start and
  completion ticks.
- Anchor capture ticks are marked. Scroll sideways and use zoom for long samples.

## Editing events

The **Events** panel edits tick expressions, operations, window IDs, source,
delivery rate and loss masks. You can add, reorder and remove events.

| Button | Adds |
| --- | --- |
| **+ Add event** | A blank event. |
| **+ EOS** | The configured EOS token, which closes a window. A plain `close` operation is rejected. |
| **+ Tool call** | A simulated tool call: JSON arguments, a duration in ticks, and a tool window ID. |
| **+ Show tools** | Advertises tool schemas in an open window. Tick the tools to show, or enable **Randomly add additional tools** for a repeatable random extra selection. |

**Tool calls.** Tool windows exist from tick 0 and hold both the request and the
result. The request always streams one token per tick (with optional custom text,
NOOPs and anchors). **Result delivery** chooses instant or streamed results.
**Auto-generate post-result anchor** creates a marker you can use to schedule later
events after the full result arrives. See [Tools](TOOLS.md) and [Sandbox](SANDBOX.md).

Segmented content (text with per-segment loss) is edited in Source JSON.

## Source JSON

Source JSON exposes the entire schema, including segmented losses and optional
metadata. **Apply & compile** saves your draft even if compilation fails, so you can
repair errors without losing work. Event edits and Source JSON stay in sync.

## Trajectory metadata

The **Trajectory metadata** section at the bottom of the sidebar edits the selected
trajectory's `metadata`:

- **Agent name** and **User name** set the speaker names (defaults: Kidra and User).
  Leave a field blank to omit it.
- **All properties (JSON)** shows everything else, including nested objects, tags and
  synthetic clock settings. Unrelated properties are preserved.

## Searching

The sidebar search box and the MCP `list_trajectories` tool share one query language.
It searches readable source text, shows 100 results per page, and runs in SQLite.

| Syntax | Meaning |
| --- | --- |
| `a b` | both terms (AND) |
| `a \| b` | either term (OR) |
| `-a`, `-(a b)` | exclude a term or group |
| `py*` | wildcard |
| `#coding` | has the tag `coding` |
| `@name`, `@uuid`, `@windows`, `@uses_tool`, `@state`, `@batch` | property filters |
| `@errors:false @warnings:false` | compiles without diagnostics |
| `@generation:unfinished` / `complete` | automated-generation status |

Example: `python -(math complex) #coding`. The
[full search guide](../trajectory_remix/guides/search.md) lists every property.

## Other windows and menus

| Where | What it does |
| --- | --- |
| **View → Dataset Metrics** | Coverage, token and timing statistics. See [Metrics](METRICS.md). |
| **View → Trajectory Guidelines** | The same guidance bundle MCP agents get from `get_guidance`, with rendered and source views per guide. Select a scenario or the current trajectory. |
| **Edit Custom Guidelines** (in that window) or **Generation Settings → Custom Guidelines** | Add project-specific guide and checklist text, optionally per scenario. Standard guides stay read-only. Custom text changes the checklist hash, so changed requirements need a fresh review. |
| **Edit → Generation** | Generate samples. See [Scenario generation](SCENARIOS.md). |
| **Edit → Validation** | Review and repair saved trajectories. See [Scenario generation](SCENARIOS.md#validate-existing-trajectories). |
| **File → Migrate Tokenizer…** | See [Getting started](GETTING-STARTED.md#change-a-projects-tokenizer). |
| **Sandbox** panel | Shows the tool state and call statuses at the selected tick. |

## Previews and limits

Replay loads small ranges of frames on demand, so long trajectories stay responsive.
A trajectory can be valid yet too large to preview (over the 4 MB preview budget).
In that case the dashboard explains the limit, and exports are unaffected. See
[Project storage and limits](STORAGE.md#limits).
