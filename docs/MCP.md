# MCP setup and task examples

Remix includes an [MCP](https://modelcontextprotocol.io) server so an AI agent can read, edit, generate, review and
export trajectories in your running studio. It uses the same services as the dashboard, so an agent's edits obey the same
revision checks and locks. For how an agent should work, see [Authoring with an agent](MCP-AUTHORING.md).

## Connect

Install the MCP extra (`pip install ./native/ktremix-python -e '.[mcp]'`) and start the studio on a project:

```bash
python -m trajectory_remix graph-studio --dataset project.sqlite \
  --mcp-allow-origin http://127.0.0.1:8123
```

**Streamable HTTP** clients connect to **`http://127.0.0.1:8765/mcp`**. The studio root only serves the dashboard.

- `--mcp-allow-origin` is needed only for a **browser-based** client, and it must be that client's exact origin
  (`localhost` and `127.0.0.1` differ). Repeat it for several. Host and origin checks are separate from the REST API's
  local session token.
- Without the MCP SDK installed, the dashboard works and `/mcp` explains how to install it.
- Both current (2026-07-28) and older MCP clients can connect; the SDK picks the protocol version for each client.
- Run Remix on loopback only. This is a trusted, single-user local service.

**stdio** clients launch a small bridge that talks to the same running studio:

```json
{"mcpServers":{"trajectory-remix":{
  "command":"/absolute/path/.venv/bin/python",
  "args":["-m","trajectory_remix.mcp_server","--url","http://127.0.0.1:8765"]
}}}
```

Use `.venv/Scripts/python.exe` on Windows clients. The bridge's `--url` is the dashboard root (not `/mcp`) and doesn't
start a second project worker. Hosting the server and downloads currently needs POSIX file leases and `/tmp`, so native
Windows hosting isn't validated.

Check the connection by calling `get_graph_session`.

## Tasks

Each row is a tool name and its JSON arguments. Replace IDs and revisions with ones you've just read. These are examples
for you to copy, not calls this document makes.

| Task | Tool | Arguments |
| --- | --- | --- |
| Orient | `get_graph_session` | `{}` |
| One short guide | `get_authoring_guide` | `{"topic":"dependencies"}` |
| Find drafts | `list_graphs` | `{"query":{"stage":-3},"limit":10,"offset":0}` |
| Inspect a source | `read_graph` | `{"id":"UUID"}` |
| Inspect errors and enabled warnings | `view_diagnostics` | `{"id":"UUID"}` |
| Inspect topology | `view_graph` | `{"id":"UUID"}` |
| Inspect event text | `view_event` | `{"id":"UUID","event_id":"think"}` |
| Create an event | `create_event` | `{"id":"UUID","expected_revision":2,"event_id":"note","window":"notes","kind":"agent_text","content":"Confirmed."}` |
| Post initial tool schemas | `post_tool_schema` | `{"id":"UUID","expected_revision":3,"tool_ids":["chat"]}` |
| Change dependencies | `update_event` | `{"id":"UUID","event_id":"think","expected_revision":2,"after":["input"]}` |
| Mark an intentional Auto split | `patch_graph` | `{"id":"UUID","expected_revision":3,"patch":{"metadata":{"split":"Auto"}}}` |
| Generate (only when authorized) | `generate_graphs` | `{"requests":[{"seed":137,"overrides":{"domain":"software_engineering"}}],"instructions":{}}` |
| Review a saved detached source | `review_graphs` | `{"trajectory":"UUID","expected_revision":4,"mode":"standard"}` |
| Job status | `inspect_graph_job` | `{"id":"JOB_UUID"}` |
| Outcomes, paged | `graph_job_items` | `{"id":"JOB_UUID","limit":10,"offset":0}` |
| Logs, by cursor | `graph_job_logs` | `{"id":"JOB_UUID","after":0,"limit":10}` |
| Pause, resume or cancel | `control_graph_job` | `{"id":"JOB_UUID","action":"pause"}` |
| Resume an exhausted endpoint | `resume_graph_endpoint` | `{"endpoint":"local_draft"}` |
| Export Ready sources | `export_graphs` | `{"options":{"compiled":false,"minimum_stage":3}}` |
| Export Ready, compiled | `export_graphs` | `{"profile":"training","options":{"compiled":true}}` |
| List finished download paths | `graph_job_artifacts` | `{"id":"JOB_UUID"}` |
| Find open comments | `list_graph_comments` | `{"status":"open","limit":20}` |
| Comments on one trajectory | `list_graph_comments` | `{"trajectory":"UUID","status":"all"}` |
| Ask the user something | `add_graph_comment` | `{"trajectory":"UUID","target":{"kind":"window","id":"doc"},"body":"Should this stay open?"}` |
| Reply in a comment's thread | `reply_graph_comment` | `{"id":"COMMENT_UUID","body":"Should c own the close instead?"}` |
| Trajectories with open comments | `list_graphs` | `{"query":{"comments":"open"},"limit":10}` |
| Say a comment is fixed (the user then reviews it) | `address_graph_comment` | `{"id":"COMMENT_UUID","note":"Moved close_window to event 13."}` |

Use `configure_window` to set window properties, `post_chat_message` to add user or
agent chat, and `signoff` to record manual authoring evidence. `create_graph` can initialize
a detached draft with empty windows and events. `get_authoring_guide` topics are `start`, `dependencies`,
`tools-chat`, `generation` and `export`.

## Behavior to know

- **Search** uses the same selector as REST and bulk jobs: `text`, `stage`, `split`, `tool`, and `comments` (`"open"`, `"addressed"` or `"unresolved"`).
- **Reads are bounded.** `read_graph` deliberately returns one full source, while session and status responses leave out
  big tokenizer, matrix and run payloads. Result and log pages default to 20 and max out at 100. Log lines over 2,000
  characters are cut with `line_truncated` set. Continue from the returned `next_offset` or the last log sequence.
- **Not available over MCP:** full log exports and advanced project settings (use REST or the dashboard), and browser
  Preferences and Undo history.
- **Downloads.** MCP returns file paths, never session tokens or archives. Fetching them needs the `X-Remix-Token` from the
  loopback dashboard, and artifacts expire after 24 hours.
- **Comments** are notes a person (or agent) leaves on a trajectory, event or window. `read_graph` includes the open
  ones as `open_comments`. Each carries its `replies` and a `status`: `open` (work to do), `addressed` (an agent says it's fixed;
  the user reviews it) or `resolved`. Only the user resolves; there is no MCP tool for it. They live outside the source, so they never change a revision and need no `expected_revision`.
- **A model pass is evidence, not approval.** Nothing an agent does makes a trajectory Ready.

See the [bundled Skill](../trajectory_remix/Skill.md) for the full authoring rules.
