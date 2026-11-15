# Graph MCP setup and task examples

Install `.[mcp]` and start an initialized graph project:

```bash
python -m trajectory_remix graph-studio --dataset project.sqlite \
  --mcp-allow-origin http://127.0.0.1:8123
```

Native Streamable HTTP clients connect to `http://127.0.0.1:8765/mcp`. The origin
flag is needed only for a browser client on that exact origin; repeat it if
necessary. Host/origin checks remain separate from REST's local session token.
Without the optional MCP SDK, the dashboard still works and `/mcp` explains how
to install it. Start Remix only on loopback.

For stdio, the bridge talks to that same dashboard and shared application service:

```json
{"mcpServers":{"trajectory-remix":{
  "command":"/absolute/path/.venv/bin/python",
  "args":["-m","trajectory_remix.mcp_server","--url","http://127.0.0.1:8765"]
}}}
```

Use `.venv/Scripts/python.exe` on Windows clients. The server/artifact runtime
currently requires POSIX file leases and `/tmp`; native Windows hosting is not
validated. The bridge does not start a second project worker.

Each row below is a tool name and its JSON arguments. Substitute current IDs and
revisions returned by reads; these are examples, not calls made by this document.

| Task | Tool | Arguments |
| --- | --- | --- |
| Orient | `get_graph_session` | `{}` |
| One short guide | `get_authoring_guide` | `{"topic":"dependencies"}` |
| Find drafts | `list_graphs` | `{"query":{"stage":-3},"limit":10,"offset":0}` |
| Inspect source | `read_graph` | `{"id":"UUID"}` |
| Change dependencies | `patch_graph_event` | `{"id":"UUID","event_id":"think","expected_revision":2,"patch":{"after":["input"]}}` |
| Mark intentional Auto split | `patch_graph` | `{"id":"UUID","expected_revision":3,"patch":{"metadata":{"split":"Auto"}}}` |
| Generate, only when authorized | `generate_graphs` | `{"requests":[{"seed":137,"overrides":{"domain":"software_engineering"}}],"instructions":{}}` |
| Review saved detached source | `review_graphs` | `{"trajectory":"UUID","expected_revision":4,"mode":"standard","repair":true}` |
| Status without frozen inputs | `inspect_graph_job` | `{"id":"JOB_UUID"}` |
| Outcomes/results page | `graph_job_items` | `{"id":"JOB_UUID","limit":10,"offset":0}` |
| Cursor logs | `graph_job_logs` | `{"id":"JOB_UUID","after":0,"limit":10}` |
| Pause/resume/cancel | `control_graph_job` | `{"id":"JOB_UUID","action":"pause"}` |
| Exhausted endpoint Resume | `resume_graph_endpoint` | `{"endpoint":"local_generator"}` |
| Raw Ready export | `export_graphs` | `{"options":{"compiled":false,"minimum_stage":3}}` |
| Compiled Ready export | `export_graphs` | `{"profile":"training","options":{"compiled":true}}` |
| List completed download paths | `graph_job_artifacts` | `{"id":"JOB_UUID"}` |

Search uses the same selector as REST/bulk: `text`, `stage`, `split`, `tool`.
`read_graph` returns one full source intentionally. Session/status responses omit
large tokenizer/matrix/run payloads. Result/log pages default to 20, max 100;
logs truncate individual lines at 2,000 characters with `line_truncated` set.
Continue from returned `next_offset` or the last log sequence. Full retained log
exports and advanced project settings are available through REST/the dashboard.
Preferences and Undo history belong to the browser and are not MCP state.

Download paths require `X-Remix-Token` from the loopback dashboard bootstrap;
MCP does not return session tokens or binary archives in tool responses. Artifacts
expire after 24 hours. A model pass is recorded evidence, never automatic Ready
approval. See the [bundled Skill](../trajectory_remix/Skill.md) for authoring rules.
