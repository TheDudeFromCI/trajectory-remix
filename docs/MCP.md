# MCP setup and tool overview

Trajectory Remix includes an [MCP](https://modelcontextprotocol.io) server so AI
agents can create, inspect, edit, review and repair trajectories in your running
project. It works over **Streamable HTTP** (at `/mcp`) and **stdio**. Both share the
dashboard's dataset, tokenizer and undo history, so agent edits appear in the
dashboard on its next refresh (about 1.5 seconds). Pending browser edits are
protected by revision checks.

This page covers connecting a client and the list of tools. Day-to-day editing
rules are in the [MCP authoring reference](MCP-AUTHORING.md).

## Setup

Install the MCP extra in the dashboard's environment (`scripts/install.sh` already
does this), then start the dashboard on an initialized project:

```bash
python -m pip install -e '.[mcp]'
python -m trajectory_remix --dataset project.sqlite
```

Restart the dashboard after installing the extra. Without it, `/mcp` returns `503`
with install instructions and the rest of the dashboard still works.

This is a trusted, single-user local service, not an authenticated public server.
MCP does not expose endpoint credentials, arbitrary file paths, shell execution
or provider settings.

### Streamable HTTP and llama.cpp Web UI

Connect to **`http://127.0.0.1:8765/mcp`**. The dashboard root `/` only serves the
dashboard, so the URL must end in `/mcp`.

For a browser-based client such as the llama.cpp Web UI on port 8123, allow its
origin when starting Remix:

```bash
python -m trajectory_remix --dataset project.sqlite \
  --mcp-allow-origin http://127.0.0.1:8123
```

In the client, choose the `streamable_http` transport, enter the URL above, and leave
authentication unset. llama.cpp's `--ui-mcp-proxy` may stay enabled; include `/mcp`
in the upstream URL. A proxy must pass POST bodies, the `Accept` and `Content-Type`
headers, and `Mcp-Session-Id` / `Mcp-Protocol-Version`, and allow streaming responses.

About origins:

- Use the exact address shown in your browser: `localhost` and `127.0.0.1` are
  different origins. Repeat `--mcp-allow-origin` to allow several.
- Wildcards and `null` origins are rejected. Requests with no `Origin` header (such
  as curl) and same-origin dashboard requests are allowed.
- Allowing an origin gives that UI access to the authoring tools. The `/api/`
  dashboard routes keep their own session-token and same-origin checks.

### Stdio clients

Configure your client to launch the bridge, using absolute paths:

```json
{
  "mcpServers": {
    "trajectory-remix": {
      "command": "/absolute/path/trajectory-remix/.venv/bin/python",
      "args": [
        "-m", "trajectory_remix.mcp_server",
        "--url", "http://127.0.0.1:8765"
      ]
    }
  }
}
```

On Windows use `.venv/Scripts/python.exe`. The `trajectory-remix-mcp --url …` entry
point is equivalent. Match `--url` to your dashboard port. Unlike HTTP clients, the
bridge's `--url` is the dashboard **root**, not `/mcp`.

Keep the dashboard running; closing the client leaves it running. The bridge accepts
only loopback URLs (`127.0.0.1`, `localhost`, `[::1]`) and fetches the same local
session token as the browser, refreshing it after a restart.

### Check the connection

Call `get_session` from your client. It returns the revision, generation status,
tokenizer fingerprint and special-token spellings.

To test HTTP directly, independent of any client:

```bash
curl -i http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl-test","version":"1"}}}'
```

Expect `200`, an initialize result, and an `Mcp-Session-Id` header. Send that header
and `Mcp-Protocol-Version` on later requests (`notifications/initialized` returns
`202`, then `tools/list` and `tools/call` return `200`). End the session with
`DELETE /mcp`. After a server restart, old session IDs return `404`, so initialize
again. To test CORS, repeat the first request with `-H 'Origin: http://127.0.0.1:8123'`
and check `Access-Control-Allow-Origin`.

### Troubleshooting

- **`405 Method Not Allowed` on `POST /`:** the client is using the dashboard root.
  Use `/mcp`.
- **`503` from `/mcp`:** the MCP extra is not installed. Install it and restart.
- **`403` with a `detail` message:** the request's `Origin` is not allowed. Add it
  with `--mcp-allow-origin`.
- **A `feature_disabled` `403`:** this is not a Remix error. Check the access settings
  and logs of the proxy in front of Remix.
- If direct curl requests work but proxied ones fail, compare the proxy's upstream
  path, method, `Origin`, `Accept` and session headers against the working requests.

## Learn one task at a time

An agent should start with `get_authoring_guide()`, which returns a short starting
page. Ask for one `topic` at a time rather than loading the whole manual:

| Topic | What it teaches |
| --- | --- |
| `first` | [Create a tiny trajectory: start ticks, token streams, closure](../trajectory_remix/guides/first.md) |
| `parallel` | [Overlap independent specialist work and anchor follow-ups](../trajectory_remix/guides/parallel.md) |
| `edit` | [Repair wording and timing with targeted edits](../trajectory_remix/guides/edit.md) |
| `sandbox` | [Seed files and verify visible reads](../trajectory_remix/guides/sandbox.md) |
| `tools` | [Choose a small starter tool set](../trajectory_remix/guides/tools.md) |
| `review` | [Semantic review checklist before sign-off](../trajectory_remix/guides/review.md) |
| `search` | [Search query syntax](../trajectory_remix/guides/search.md) |
| `metrics` | [Dataset metrics and annotation repair](../trajectory_remix/guides/metrics.md) |
| `reference` | [Complete manual (Skill.md)](../trajectory_remix/Skill.md) |
| `example` | [Complete build-graph trajectory JSON](../examples/build-graph-analysis.json) |

`example` returns the full source JSON as a string in `content`, without changing the
project. To use it, parse the string and pass it as `sample` to `create_trajectory`.
It was timed for another tokenizer, so run `validate_trajectory` and retime if needed.
To reduce the tool list shown to a model, prune it in your client; the server does
not restrict tools.

## Tool catalogue

All responses are JSON text plus MCP `structuredContent`. Errors set `isError` with
a message and, where applicable, a code (`conflict`, `busy`, `not_found`,
`invalid_arguments`, `unknown_tool`, `quota_exceeded`). Unknown arguments are rejected.
A failed `validate_trajectory` is a successful inspection that returns `valid: false`.

### Guidance and session

| Tool | Purpose |
| --- | --- |
| `get_session` | Revision, generation status, tokenizer fingerprint, special tokens, slot limit. |
| `get_authoring_guide` | A short guide, one lesson by `topic`, the full manual, or the example. |
| `get_guidance` | The full guidance bundle (also in **View → Trajectory Guidelines**). Pass `scenario` before creating, or `sample_id` for a saved review contract. |

### Finding trajectories

| Tool | Purpose |
| --- | --- |
| `list_trajectories` | Search with the [dashboard query language](../trajectory_remix/guides/search.md). |
| `query_trajectories` | Filter metadata, tags and counts. See [Storage](STORAGE.md#metadata-and-queries). |
| `get_metadata_counts` | Group matches by metadata value, tag or count. |
| `get_trajectory` | Complete source JSON, including invalid drafts. |
| `get_trajectory_metadata` | Everything except `replay`. |

### Creating and editing

| Tool | Purpose |
| --- | --- |
| `create_trajectory` | From full JSON, an empty draft, or `template: "demo"`. |
| `duplicate_trajectory`, `replace_trajectory`, `delete_trajectory` | Copy, fully replace, or remove. |
| `update_trajectory_metadata` | JSON merge-patch of ID, defaults, metadata and other attributes. |
| `list_events`, `get_event` | Read events with their current addresses. |
| `create_event`, `update_event`, `move_event`, `duplicate_event`, `delete_event` | Edit individual events. |
| `patch_event_content` | Replace text or segments, or patch character ranges. |
| `rename_window` | Rename a window across all events. |
| `create_tool_call` | Schedule a visible or invisible simulated tool call. |
| `apply_batch` | Up to 100 edits applied atomically, with optional dry run. |
| `diff_trajectories` | JSON Patch and optional text diff between two sources. |
| `get_edit_history`, `undo_edits`, `redo_edits` | Shared source history. |
| `save_dataset` | Report a checkpoint. Edits are already saved. |

### Inspecting

| Tool | Purpose |
| --- | --- |
| `validate_trajectory` | Compile, and optionally run semantic LLM review (`semantic: true`). |
| `get_diagnostics` | Compiler errors and warnings. |
| `get_anchors` | Resolved anchor ticks, phases and owners. |
| `get_window_history` | One window's lifecycle, text and token rows. |
| `preview_snapshots` | Window state at ticks across a range. |
| `get_sandbox_state` | Sandbox state and call statuses at a tick. |

### Review and repair

| Tool | Purpose |
| --- | --- |
| `review_trajectory` | Checklist, guide, source and diagnostics in one call, with no model API. |
| `get_review_checklist` | Just the checklist and results schema. |
| `repair_trajectory` | Targeted atomic repairs, with dry run. |
| `record_trajectory_review` | Record per-rule results; code derives Validated (2) or Rejected (-2). |
| `sign_off_trajectory` | Unstructured sign-off for manual samples. |
| `get_generation_record`, `get_generation_analytics` | Evidence about how a sample was generated. |

### Simulated tool workbenches

| Tool | Purpose |
| --- | --- |
| `list_simulated_tools` | Exact schemas and availability. |
| `create_tool_session`, `list_tool_sessions`, `get_tool_session`, `reset_tool_session`, `delete_tool_session` | Manage isolated workbenches. |
| `call_simulated_tool` | Run a tool, optionally recording it in a trajectory. |
| `record_tool_interaction` | Record an earlier workbench call without re-running it. |

### Dataset metrics

`get_dataset_metrics`, `refresh_dataset_metrics`, `get_dataset_metrics_status`,
`cancel_dataset_metrics_refresh`, `get_dataset_metric_distribution`,
`query_dataset_metrics` and `get_metric_annotation_context`. See [Metrics](METRICS.md).

## Next

[MCP authoring reference](MCP-AUTHORING.md): editing rules, batches, review and
repair, tool workbenches, and inspection limits.
