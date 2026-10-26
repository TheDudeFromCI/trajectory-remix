# Synthetic tools reference

This document describes the **38 built-in synthetic tools** shipped with Trajectory Remix: **32 model-visible tools** and **6 author-only tools**. It covers their argument contracts, results, persistent state, timing, and use in authored trajectories. These are **not** the MCP commands that edit datasets. The MCP authoring API is documented in [MCP.md](MCP.md); the low-level execution contract and custom-tool interface are in [SANDBOX.md](SANDBOX.md).

The authoritative implementations are the Python modules in [`trajectory_remix/tools/`](../trajectory_remix/tools/). Call MCP `list_simulated_tools` to retrieve the actual schemas, including installed custom tools. In that listing, author-only tools are marked `invisible: true`. The table below summarizes the built-ins; the sections that follow explain every one.

## Index

| Area | Model-visible | Author-only |
| --- | --- | --- |
| Arithmetic and random values | `calculator`, `random` | — |
| Files and change detection | `file_write`, `file_read`, `file_stat`, `file_patch`, `file_list`, `file_search`, `file_delete`, `file_copy`, `file_move`, `file_rename`, `file_watch` | — |
| Memory and task notes | `memory_save`, `memory_search`, `note_save`, `note_list`, `note_delete` | — |
| Skills | `skill_save`, `skill_list`, `skill_load` | — |
| Clock and timers | `time_now`, `time_set`, `timer` | `time_advance` |
| Chat and delegated conversations | `chat`, `subagent`, `subagent_get`, `subagent_list`, `subagent_close` | — |
| Runtime and hardware | `runtime_status`, `system_diagnostics` | `system_diagnostics_set`, `hardware_load_set` |
| Network | `network_request` | `network_set` |
| Permission and fault scenarios | — | `tool_permissions_set`, `io_failure_set` |

## Operating model

- **Simulation, not access to real resources.** Built-in files exist as UTF-8 text in a virtual RAM filesystem. Memory, notes, skills, conversations, network responses, and hardware readings are synthetic. `network_request` never sends an HTTP request; `subagent` never launches an LLM; `system_diagnostics` never probes the host. Neither file tools nor `calculator` execute arbitrary shell/Python code.
- **One sandbox per trajectory.** Recompilation starts from fresh deterministic state. A sandbox is a tool-owned dictionary, initially holding `tick_rate`; most tools add state only when used. Separate MCP workbench tool sessions have separate ephemeral sandboxes. Executing a tool in a workbench does not automatically transplant its state into a trajectory.
- **Tick execution.** Kidra generates a tool request in a tool window, one token per tick. The call starts **the tick after the request's final token**. The runtime calls `run(args, state, windows)` on each execution tick until it reports completion. Calls may overlap; each runs in stable source order on a shared virtual world. A tool can produce zero or multiple observations while it is running.
- **Results and masks.** Normal tool outputs are JSON envelopes: `{"ok":true,"result":...}` or `{"ok":false,"error":"..."}`. Visible observations are externally appended to the **same tool window** as the request, and excluded from training loss; Kidra's request is generated content. Authors can choose instant or streamed result delivery. A reported failure is an observation, not automatic trajectory termination.
- **Visibility.** The six author-only tools can only be invoked through `invisible-tool-call`. Any otherwise visible tool may also be invoked invisibly to model external activity, without adding a request/result window. Some individual parameters, such as `chat.username` and `subagent.response`, are also invisible. Public model schemas omit them and reject them in visible calls. `show_tools` exposes selected public schemas to Kidra but does not act as an execution allowlist.
- **Time and cancellation.** The synthetic clock advances with logical ticks (`tick_rate` defaults to 10 ticks/second), not host sleep or the count of tool calls. The default clock seed is `2030-01-01T09:00:00+00:00` unless configured in `metadata.synthetic_clock`. Generated EOS on a tool window cancels unfinished activity; built-in pending timers, watches, network calls, file operations, and subagents clean up, but completed mutations are not rolled back. Each invocation has a 10-second real-time safety deadline, independent of simulated duration.
- **Quotas and ordering.** Built-ins obey sandbox byte limits and tool-specific field limits. The trajectory compiler has its own tick/row limits. Within the same tick, a later tool can observe an earlier tool's completed mutation, but not vice versa. Schedule dependent model reasoning after the relevant tool result has been delivered.

### Authoring examples

Schedule a visible tool call using the **MCP authoring operation** `create_tool_call` (not itself a synthetic tool):

~~~json
{
  "sample_id": "example",
  "tick": "10",
  "tool": "file_read",
  "window": "read-config",
  "arguments": {"path": "/config.txt", "line_numbers": true},
  "auto_anchor": true
}
~~~

The call's `result_anchor` identifies the tick where the *last result token is delivered*. A later event can use `{N_TICK:1}+1` (substitute the returned anchor ID); do not assume the completion or delivery tick in advance.

To make an external action happen without Kidra requesting or seeing it, insert a replay event such as:

~~~json
{
  "op": "invisible-tool-call",
  "tool": "file_write",
  "arguments": {"path": "/config.txt", "content": "mode=debug\n"},
  "source": "external-editor"
}
~~~

This changes the world, not Kidra's visible context. A later `file_read` or `file_watch` is needed for observation. Author-only configuration calls use this same event operation.

## Arithmetic and random values

### `calculator`

**Arguments:** required `expression` (string, 1–4096 characters).

**Result:** `{"value": number}`.

Safely evaluates numeric expressions with `simpleeval`: `+`, `-`, `*`, `/`, `//`, `%`, `**`; constants `pi`, `e`, `tau`; and `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `sqrt`, `log`, `log10`, `log2`, `exp`, `degrees`, `radians`, `abs`, `round`, `min`, `max`, `floor`, `ceil`. Angles are radians. Example: `{"expression":"sin(pi/2) + log(8,2)"}` yields `{"value":4}`. Only numeric literals and allowed arithmetic/function syntax are accepted; expressions exceeding 256 AST nodes, non-finite answers, and oversized integers fail. It does not evaluate arbitrary Python.

### `random`

**Arguments:** required `kind` = `"int"`, `"bool"`, `"float"`, or `"choice"`; optional numeric `min` / `max`; required nonempty `choices` array for `choice` (at most 1024 entries).

**Result:** `{"value": ...}` with the requested type.

`int` samples inclusive integer bounds (default 0–100; endpoints must be integers within ±(2^53−1)); `float` samples between bounds (default 0–1); `bool` draws a boolean; `choice` selects an element, including a JSON object or array. Example: `{"kind":"choice","choices":["red","green","blue"]}`. Draws are deterministic for the trajectory seed and saved draw counter; successful draws advance the counter, failed ones do not. Not suitable for cryptographic use.

## Virtual filesystem

Files are virtual UTF-8 text keyed by normalized POSIX-style paths. Paths receive a leading `/`; `..` traversal, backslashes and NUL are rejected. Directories are not separate filesystem objects. Content is versioned using `sha256:<hex>`; historical versions are retained as artifacts and can be read by path/version even after a file changes or is deleted. No tool accesses host files. Version checks use **content hashes**, not monotonically increasing revision numbers.

### `file_write`

**Arguments:** required `path` (string), `content` (string); optional `expected_version` (string).

**Result:** `{"saved":"/path","version":"sha256:..."}`.

Creates or entirely replaces the current virtual file. Without `expected_version`, an existing file is overwritten. With it, the current file must exist and have the specified hash, or the write fails. Example: `{"path":"/src/app.py","content":"print(42)\n"}`. The old historical artifact remains available.

### `file_read`

**Arguments:** required `path`; optional `version` (historical hash), `start_line` and `end_line` (positive, inclusive 1-based line numbers), `line_numbers` (boolean).

**Result:** `{"path":...,"version":...,"content":...}`; line-sliced results additionally have `start_line`, `end_line`, `total_lines`.

Reads current content by default, or a pinned historical version if `version` is supplied. Example: `{"path":"/src/app.py","start_line":1,"end_line":20,"line_numbers":true}`. Line-ending text is preserved; ranges beyond EOF may return empty content, and explicitly reversed ranges fail. Adding line numbers changes only returned text, not stored content.

### `file_stat`

**Arguments:** required `path`.

**Result:** `{"path":...,"version":"sha256:...","bytes":number}`.

Gets the current content hash and UTF-8 byte size without returning file contents. Use the version for an optimistic `expected_version` write or a pinned artifact reference to `subagent`. Missing files fail.

### `file_patch`

**Arguments:** required `path` and **either** a single `old`/`new` pair **or** `edits` (1–100 objects with `old` and `new`); optional `expected_version`.

**Result:** the same saved path/version shape as `file_write`.

Replaces an exact text occurrence. Each nonempty `old` must match **exactly once** in the current working content. Batch edits execute sequentially but only commit if all succeed, so a failed later edit leaves the actual file unchanged. Example: `{"path":"/settings.ini","edits":[{"old":"debug=false","new":"debug=true"}]}`. The version guard detects concurrent stale edits. This is not a unified-diff or fuzzy patch tool.

### `file_list`

**Arguments:** optional `glob` (string, at most 1024 characters).

**Result:** a sorted array of matching current file paths.

Example: `{"glob":"/src/*.py"}`. Matching is case-sensitive; glob `*` may span a path separator. A permission-denied path is omitted from a visible listing. Historical-only artifacts are not listed.

### `file_search`

**Arguments:** required `query` (nonempty text, up to 4096 characters); optional `glob`, `regex` (default false), `case_sensitive` (default true), `context_lines` (0–20, default 0), and `max_results` (1–1000, default 100).

**Result:** `{"matches":[{"path":...,"line":1,"text":...,"before":[...],"after":[...]}],"truncated":false}`.

Searches current file **lines** in sorted path/line order. The query is literal unless `regex:true`, when it is a Python regular expression. Context arrays contain surrounding lines. Stops at the result cap and sets `truncated:true` if further matches are found. Visible searches skip paths denied for `file_search`.

### `file_delete`

**Arguments:** required `path`.

**Result:** `{"deleted":"/path"}`.

Deletes a current virtual file and fails if it is absent. Previously saved historical artifacts remain readable with `file_read.version`.

### `file_copy`

**Arguments:** required `source`, `destination`; optional `overwrite` (default false), `expected_version` (source hash).

**Result:** `{"source":...,"destination":...,"version":"sha256:..."}`.

Copies text to a different path, leaving the source. An existing destination is rejected unless `overwrite:true`; equal source/destination is rejected. The source version guard is checked before copying.

### `file_move`

**Arguments and result:** same as `file_copy`.

Atomically transfers current content to the destination and removes the current source. Same destination conflict, overwrite, and source-version rules as `file_copy`. Historical artifacts for either path remain.

### `file_rename`

**Arguments and result:** same as `file_move`.

A direct alias in behavior for `file_move`, accepting `source` and `destination` rather than separate filename fields. It is not a directory-renaming operation.

### `file_watch`

**Arguments:** required `path`; optional `events` (unique subset of `created`, `modified`, `deleted`; default all three), `count` (1–1024, default 1), `timeout_seconds` (default 100), or `timeout_ticks` (explicit alternative).

**Result:** each match emits `{"path":...,"event":...,"version":...,"tick":...}`; the call completes after `count` matches or fails when timed out.

Long-running watch of a **virtual path**, not the host filesystem. It records the file's baseline hash at dispatch, then compares hashes once per logical tick. Example: `{"path":"/report.txt","events":["modified"],"count":1,"timeout_seconds":20}`. A created-then-deleted file between polls is not observed. Completion of delayed writes determines when changes become visible. Multiple change observations can be produced by one call. EOS cancellation removes the watcher.

## Memory and task tracking

### `memory_save`

**Arguments:** required `key`, `text` (both strings).

**Result:** `{"saved":key}`.

Stores or replaces one text memory under a stable key in the trajectory sandbox. It does not write to the user's real memory system or an external vector database.

### `memory_search`

**Arguments:** required `query` (string).

**Result:** up to eight `{"key":...,"text":...,"score":number}` entries.

Lowercases and tokenizes query/memory text into sets of word-like terms, ranks by intersection-over-union (Jaccard lexical similarity), breaks ties by key, and excludes zero-overlap records. This is deterministic text overlap, **not** embeddings or semantic search.

### `note_save`

**Arguments:** `name` is required. When creating a new task, also provide `description`, `priority` (`critical`, `very high`, `high`, `medium`, `low`, or `very low`), and `status` (`not started`, `doing`, `paused`, `blocked`, or `done`). The optional `blocked_by` array contains up to 64 distinct task names.

**Result:** `{"saved":name}`.

Creates or updates a TODO task, distinct from the searchable text records handled by `memory_save`. On updates, only `name` is required; omitted fields remain unchanged. Names uniquely identify tasks across priorities. Dependencies may refer to tasks not yet created, but cannot form cycles (including self-dependencies). A task cannot be marked `done` until every task named by `blocked_by` exists and is `done`. A finished prerequisite cannot be reopened or deleted while a completed task depends on it. To clear blockers, supply an empty array.

Example: `{"name":"security-bug","description":"Fix critical security vulnerability","priority":"critical","status":"not started","blocked_by":["update-auth-lib"]}`.

### `note_list`

**Arguments:** none (`{}`).

**Result:** a JSON object with six priority buckets, ordered critical → very low; each bucket maps task names to `{"description":...,"status":...,"blocked_by":[...]}` objects.

Returns all tasks, including empty priority buckets. This is identical in structure to the automatic `notes` context window.

### `note_delete`

**Arguments:** required `name`.

**Result:** `{"deleted":name}`.

Deletes a named TODO task; missing names fail. Remaining tasks keep their `blocked_by` references, so an unfinished or deleted dependency prevents completion. A prerequisite referenced by a completed task cannot be deleted.

### Automatic notes window

When a trajectory uses `note_save`, `note_list`, or `note_delete` (including invisible calls), the compiler reserves a `notes` window at tick 0, initially containing JSON with all six empty priority buckets. It counts toward the project window-slot limit, is externally managed, and must not be authored, generated into, or closed by the model. Task mutations replace its JSON content in phase B at the mutation tick, keeping the latest task state available to the agent without calling `note_list`. Replacements are loss-masked; prior task-list tokens are evicted. Multiple changes in one tick are ordered by the source events. Samples with no note tools have no extra notes window.

## Stored skills

### `skill_save`

**Arguments:** required `name`, `content`.

**Result:** `{"saved":name}`.

Creates or replaces a named **text** skill inside the sandbox. This is data persistence, not Python module installation or a host skill registry.

### `skill_list`

**Arguments:** none (`{}`).

**Result:** sorted array of stored skill names.

Lists the synthetic skill texts available in the current sandbox only.

### `skill_load`

**Arguments:** required `name`.

**Result:** `{"name":...,"content":...}`.

Returns the stored text skill; an unknown name fails. Reading it is an ordinary tool observation the model can use as context.

## Synthetic time and timers

### `time_now`

**Arguments:** none (`{}`).

**Result:** `{"timestamp":"2030-01-01T09:00:00+00:00","synthetic":true}` (timestamp varies with trajectory).

Reads the current simulated ISO-8601 timestamp derived from the seed/last clock anchor and elapsed ticks. Does not change the anchor.

### `time_set`

**Arguments:** **all seven keys required**: `year` (1–9999), `month` (1–12), `day` (1–31), `hour` (0–23), `minute` (0–59), `second` (0–59), and `timezone`; **each value may be `null`**.

**Result:** `{"timestamp":...,"synthetic":true}`.

Sets the synthetic local date and time. Timezone accepts an IANA zone (`America/New_York`), `UTC`, or fixed UTC offset (`+05:30` / `UTC-07:00`). Null fields are filled by deterministic seeded selection (null years from 2000–2099); missing keys are not equivalent to null. Invalid calendar dates and DST-skipped local times fail; ambiguous times choose their first occurrence. Example: `{"year":2030,"month":1,"day":1,"hour":9,"minute":0,"second":0,"timezone":"UTC"}`.

### `time_advance` — author-only

**Arguments:** required `seconds` (nonnegative number).

**Result:** `{"timestamp":...,"synthetic":true}`.

Adds elapsed simulated seconds to the synthetic clock without sleeping; daylight-saving transitions are respected. Invoke invisibly to model elapsed external time:

~~~json
{"op":"invisible-tool-call","tool":"time_advance","arguments":{"seconds":30}}
~~~

This changes the time anchor and can wake pending alarms, timers, and simulated HTTP responses.

### `timer`

**Arguments:** required `mode`; mode-specific required arguments:
- `timeout`: exactly one of `seconds` (>= 0) or `ticks` (>= 0).
- `interval`: exactly one of `seconds` (> 0) or `ticks` (> 0), plus `count` (1–1024).
- `alarm`: exactly one of `timestamp` (timezone-aware ISO timestamp) or `at_tick` (nonnegative integer). Optional `timeout_seconds` or `timeout_ticks` constrains waiting.

**Result:** `{"mode":...,"tick":...}`; simulated-seconds paths also return `elapsed_seconds`; interval firings include `firing`.

A long-running logical wait with no host sleeping. Examples: `{"mode":"timeout","seconds":0.5}` finishes after five ticks at 10 ticks/s; `{"mode":"interval","ticks":2,"count":3}` emits three observations; `{"mode":"alarm","at_tick":100}` fires on or after tick 100. Timestamp alarms use the mutable synthetic clock and can be affected by `time_set` and `time_advance`. Alarms default to a 100-simulated-second deadline unless an explicit tick timeout is selected. An interval that falls behind after a clock jump emits catch-up firings on successive ticks. EOS cancellation releases pending timer state.

## Conversation, delegation, and status

### `chat`

**Arguments:** required `content`; **invisible-only argument** `username` (nonempty string).

**Result:** `{"window":"chat","content":"<|msg|>Kidra: ...<|/msg|>\n"}` with the applicable sender name.

Appends a framed message to an **already open** window named `chat`. Visible model calls use the trajectory's `metadata.agent_name` (default `Kidra`); a visible caller cannot choose `username`. Authors can simulate another speaker with an invisible call:

~~~json
{"op":"invisible-tool-call","tool":"chat","arguments":{"username":"User","content":"Please check again."},"source":"user"}
~~~

Fails if the `chat` window is not open. This tool writes window content, not a new conversation in the synthetic subagent store.

### `subagent`

**Arguments:** required `task` (string); optional `resume` (prior conversation ID), `artifacts` (up to 32 pinned objects, each with `path` and `version`); **invisible-only argument** `response` (authored reply).

**Result:** `{"conversationId":...,"response":...}` for a successful turn; failure envelope may include `conversationId`.

Simulates a specialist conversation, **without calling a real model**. The author must supply its outcome either via the `outcome` field on the authored `create_tool_call` event or by staging a response for the exact task using an invisible `subagent` call with `response`. A visible `task` alone does not generate an answer. Example authoring operation:

~~~json
{
  "sample_id": "delegation",
  "tick": "10",
  "tool": "subagent",
  "window": "specialist-call",
  "arguments": {"task": "Review /src/app.py"},
  "duration_ticks": 20,
  "auto_anchor": true,
  "outcome": {
    "ok": true,
    "result": {"conversationId": "reviewer","response": "Review complete; one bug found."}
  }
}
~~~

At dispatch, stores a `running` synthetic conversation with a user turn containing the task and **the exact pinned artifact contents** if supplied. The returned reply becomes the assistant turn on completion; status becomes `ready`, or `failed` for an authored error. `resume` starts another turn in an existing `ready` or `failed` conversation; unknown, running, or closed conversations cannot be resumed. Reusing a *tool window name* does not resume a conversation: use `resume`. Pinned versions come from `file_stat` / `file_write` and remain stable if the current file changes. An authored failure can use `{"ok":false,"error":"..."}`. The tool records history, not background workers.

### `subagent_get`

**Arguments:** required `conversationId`.

**Result:** `{"conversationId":...,"status":...,"messages":[{"role":...,"content":...},...]}`.

Inspects the complete retained synthetic specialist conversation, including prior user requests, authored assistant replies, and current status. Missing IDs fail.

### `subagent_list`

**Arguments:** none (`{}`).

**Result:** array of `{"conversationId":...,"status":...,"turns":number}` summaries.

Lists retained specialist conversations in sandbox insertion order; `turns` is the number of completed assistant messages, not the number of arbitrary tool observations.

### `subagent_close`

**Arguments:** required `conversationId`.

**Result:** complete conversation object with its `status` now `closed`.

Closes an existing nonrunning conversation but preserves its messages. A running conversation cannot be closed this way; cancel its active tool window to stop the in-flight call. Further `subagent` resumes after closure fail.

### `runtime_status`

**Arguments:** none (`{}`).

**Result:** `{"clock":...,"synthetic":true,"calls":number,"tasks":{"not started":n,"doing":n,"paused":n,"blocked":n,"done":n},"subagents":[...]}`.

Summarizes the synthetic clock, the sandbox's legacy completed-call counter, note counts by status, and retained subagent lifecycle states. Useful for coordinator status checks while multiple delegated calls are in progress. It is not a list of live operating-system processes or a substitute for the author-facing per-call timeline.

## Simulated hardware and workload

### `system_diagnostics`

**Arguments:** none (`{}`).

**Result:** `{"synthetic":true,"cpu_usage_percent":...,"cpu_cores":...,"cpu_model":...,"ram_total_bytes":...,"ram_used_bytes":...,"vram_total_bytes":...,"vram_used_bytes":...,"gpu_model":...,"os":...,"architecture":...}`.

Reports author-controlled hardware metrics, **not actual host usage**. Defaults: simulated Linux/x86_64, eight CPU cores, zero CPU usage, 16 GiB RAM (4 GiB used), 8 GiB VRAM (0 used), and generic CPU/GPU model names. Useful for synthetic resource constraint and diagnostic scenarios.

### `system_diagnostics_set` — author-only

**Arguments:** one or more of `cpu_usage_percent` (0–100), `cpu_cores` (1–4096), `cpu_model`, `ram_total_bytes`, `ram_used_bytes`, `vram_total_bytes`, `vram_used_bytes`, `gpu_model`, `os`, `architecture`.

**Result:** `{"synthetic":true,...updated hardware fields...}`.

Merges fields into the synthetic diagnostics; omitted values retain prior settings. Rejects used RAM/VRAM greater than their respective totals. Example:

~~~json
{"op":"invisible-tool-call","tool":"system_diagnostics_set","arguments":{"cpu_usage_percent":91,"ram_used_bytes":8589934592}}
~~~

### `hardware_load_set` — author-only

**Arguments:** at least one of `file_io_ticks` (nonnegative integer, up to 99999), `operation_ticks` (object mapping one or more of the ten non-watch file operations to nonnegative tick delays).

**Result:** merged configuration, such as `{"file_io_ticks":5,"operation_ticks":{"file_read":2}}`.

Adds deterministic **execution latency**, not CPU load, to `file_read`, `file_write`, `file_patch`, `file_delete`, `file_stat`, `file_list`, `file_copy`, `file_move`, `file_rename`, and `file_search`. Per-tool overrides beat the default. Delay is sampled when a call starts and is additive to legacy `duration-ticks`; changing the config does not retime already-running calls. The mutation happens on completion. Set an override to zero to remove that tool's added delay.

## Simulated HTTP

### `network_request`

**Arguments:** required `url` (HTTP/HTTPS URL without embedded credentials); optional `method` (`GET` default, or `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`), `headers` (string map), `body` (text), `timeout_seconds` (default 30, max 86400).

**Result:** `{"url":...,"method":...,"status":200,"headers":{},"body":"...","elapsed_seconds":...}`, or a tool-failure envelope.

Uses **only** routes configured by `network_set`. A matching route may impose simulated latency, return sequential responses or inject a transport error. HTTP statuses such as 404/429/503 are *successful tool transport outcomes* (`ok:true`); the model must interpret the status itself. DNS/TLS/reset/timeout problems become `ok:false` tool failures. Never makes a host network request, follows redirects, or retries automatically. `HEAD` returns an empty body. Waiting requests can be cancelled; the selected response has already been consumed.

### `network_set` — author-only

**Arguments:** required `routes` (array, at most 128; `[]` clears fixtures). Each route has `url` (URL glob), optional `method` (default `GET`), optional exact `request_body` and matching subset `request_headers`, and required nonempty `responses` (max 128). Response fields: `status` (100–599, default 200), `headers`, `body` (text), `latency_seconds` (0–86400), or `error` (`dns`, `connection_refused`, `connection_reset`, `tls`, `timeout`).

**Result:** `{"routes":number}`.

Replaces the fixture list and resets per-route response cursors. Routes are tested in declared order; the first matching method, URL glob, exact body, and required header subset wins. Request header names match case-insensitively. Each new request consumes the next response in that route's sequence; its last response repeats. In-flight requests retain the response selected at dispatch even if routes are changed. Example:

~~~json
{
  "op": "invisible-tool-call",
  "tool": "network_set",
  "arguments": {
    "routes": [{
      "url": "https://api.example.test/items",
      "method": "GET",
      "responses": [
        {"status": 503, "body": "Retry later", "latency_seconds": 0.5},
        {"status": 200, "body": "{\"items\":[1,2]}"}
      ]
    }]
  }
}
~~~

A sequence such as 503 then 200 lets a trajectory teach explicit retry behavior; the two calls must be authored in separate, causally valid windows/times.

## Author-only permission and failure injection

These tools are **training controls**, not a security boundary around trusted custom Python tools. Both act on future matching dispatches. Author-only invocations bypass simulated permissions but not I/O fault rules.

### `tool_permissions_set` — author-only

**Arguments:** optional `default` (`allow` or `deny`; default `allow`) and `rules` (up to 256). Each rule requires `decision` (`allow`/`deny`), with optional `tool` glob, `path` glob, `remaining` (1–100000), and `message`.

**Result:** `{"default":...,"rules":[...]}` (the installed policy).

Replaces the entire permission policy. The **first matching** rule determines permission for each relevant target: normalized `path`, `source`, `destination`, or HTTP `url`. Both source and destination of transfers must be permitted; one permitted endpoint does not override a denied other endpoint. A rule without `remaining` is persistent; a limited rule is consumed on matching dispatches. A rejected visible call returns `Permission denied: ...` before execution. `file_list`/`file_search` silently filter denied paths instead of exposing them. Invisible calls bypass this policy. Example:

~~~json
{"op":"invisible-tool-call","tool":"tool_permissions_set","arguments":{
  "default":"allow",
  "rules":[{"tool":"file_write","path":"/reports/*","decision":"deny","remaining":1,"message":"User rejected write"}]
}}
~~~

### `io_failure_set` — author-only

**Arguments:** required `rules` array (0–256). Each rule may specify `tool` glob, `path` glob, `remaining`, `message`, and `code` (for example `EIO`, `ENOSPC`, `EACCES`, `ETIMEDOUT`).

**Result:** `{"rules":[...]}` (the installed fault policy).

Replaces deterministic fault rules; `{"rules":[]}` clears them. The first matching rule injects an error **once at dispatch**, after simulated permission checks but before effects or artificial latency. Applies to the ten ordinary file operations, `file_watch`, and `network_request`; unlike simulated permissions, faults may affect invisible file/network calls too. `remaining:1` models a transient error; omitting it models a persistent one. Example:

~~~json
{"op":"invisible-tool-call","tool":"io_failure_set","arguments":{"rules":[
  {"tool":"file_read","path":"/data/*","code":"EIO","remaining":1,"message":"Temporary I/O fault"}
]}}
~~~

## Design and correctness notes

1. **Keep cause and observation separate.** Configure network routes, files, diagnostic changes, and specialist outcomes before visible calls depend on them. An invisible side effect supplies no automatic model-visible text.
2. **Use parallel tool windows for parallel calls.** Two independently generating requests can overlap, each with its own request/result window. The sandbox state is shared, and tool invocations are ordered deterministically by the replay scheduler. A model window cannot have overlapping competing generated streams.
3. **Pin reviewed files.** Obtain `file_stat.version` or the version returned by `file_write`, then pass that specific hash through `subagent.artifacts`. A later edit does not retroactively change a specialist's supplied content.
4. **Expect negative outcomes.** Simulate missing files, version conflicts, permission rejections, transient I/O faults, HTTP failures, timeouts, and cancelled calls as observable states rather than manually inventing mismatched tool results.
5. **Treat schemas as authoritative.** This reference covers the checked-in built-ins, not an open-ended custom tool directory. For field types, max lengths, visibility, and validation, inspect `list_simulated_tools` or the relevant source module. The compiler rejects invalid visible/invisible usage.

Related references: [SANDBOX.md](SANDBOX.md) (execution protocol, snapshotting, cancellation, extensions), [MCP.md](MCP.md) (authoring and tool sessions), [Skill.md](../trajectory_remix/Skill.md) (trajectory-author instructions).
