# Sandbox coordination

## External activity

`invisible-tool-call` represents an external actor changing the virtual sandbox:

```json
{
  "op": "invisible-tool-call",
  "tool": "file_write",
  "arguments": {"path": "/module.py", "content": "provided by a subagent"},
  "source": "coder",
  "duration-ticks": 0
}
```

Place it under a numeric or anchor tick key. It commits at the resolved tick plus
`duration-ticks`. Allowed tools are `file_write`, `file_patch`, `file_delete`,
`memory_save`, `note_save`, `note_delete`, `skill_save`, and `time_advance`.
The optional `source` identifies the external actor; it defaults to `external`.
It is attribution supplied by the author, not authenticated evidence of execution.

External activity has no model-owned window, request, result, loss, or tokens.
It remains in editable source and author-facing sandbox history, but is absent
from compiled token rows, model windows and training exports. Later visible
reads can expose its effects. Activity after the final visible event extends
the inspection horizon (`max_tick`) without extending the token horizon
(`token_max_tick`), idle NOOPs, or exported chunks. No agent call budget or
automatic clock increment is charged; explicit `time_advance` still changes time.
Invalid operations stop compilation with an event-path diagnostic.

Normal calls and external mutations share completion ordering: resolved tick,
then the compiler's existing stable source order. That order sorts numeric keys
before anchor expressions and preserves array order within each key. To depend
on a completion, use a later tick or put equal-tick calls in explicit array order.

MCP exposes this through `create_tool_call(..., invisible=true, source="coder")`.
The dashboard provides **+ External activity** and the event operation selector.
Both paths support source history, atomic batches, revision checks and saving.

## Persistent conversations

`subagent({"task":"Implement"})` returns `conversationId` and `response`.
`subagent({"task":"Explain your choice","resume":"conversation-1"})` sends that
conversation's retained user and assistant turns to the configured provider.
New calls without `resume` create independent conversations. Identities and
messages belong to one sandbox, and reset/deletion destroys them. The generation
builder restores retained conversations when continuing saved trajectories.

`subagent_list` returns identities, status and completed turn counts.
`subagent_get({"conversationId":"..."})` returns messages and status.
`subagent_close` retains history and prevents further resumes. States are
`running`, `ready`, `failed`, and `closed`. Failed turns can be followed up;
unknown, closed and already-running conversations cannot be resumed.

Replay never calls a provider. Each recorded subagent event supplies its response
and optionally `conversationId` in `outcome.result`. Older outcomes without an ID
receive deterministic IDs. IDs cannot be reused for unrelated new conversations,
and a recorded resumed ID must match the requested ID. A dispatch must find its
conversation available at that tick, even if its eventual completion is much
later. Running history contains the submitted task, not its future answer.
Successful completion appends the answer atomically. These checks produce tool
error observations for invalid visible calls, like other sandbox failures.
If a dispatched live turn fails, its error observation includes `conversationId`
at the top level so the coordinator can inspect or resume it. Recording preserves
that identity during offline replay as well.

## Artifacts and tasks

File writes/patches return a SHA-256 `version`. `file_stat` retrieves the current
reference without the body. A subagent can receive `artifacts` such as
`[{"path":"/module.py","version":"sha256:..."}]`. The sandbox resolves only
those versions into its delegated context. No other parent files, memories or
trajectory windows are automatically shared. Retained conversation history
keeps the originally supplied artifact data on later turns.

`file_read` accepts optional `version` for an immutable historical read. Deleting
a current file preserves historical versions. `file_write` and `file_patch`
accept optional `expected_version`; a stale version fails without changing the
file or artifact store. `file_patch` takes `path`, `old`, `new`, and replaces
exactly one nonempty unique occurrence. Version storage and retained conversation
messages count toward the existing aggregate RAM quota; neither grows unbounded.

`note_save` accepts optional `owner`, `depends_on`, `blockers`, and
`acceptance_criteria`, plus `blocked` status. Optional fields survive later updates
unless explicitly cleared. Self-dependencies and cycles fail. A task cannot be
marked done while blockers or unfinished/missing dependencies remain. Forward
references are allowed while planning. Acceptance criteria remain descriptive.
`runtime_status` reads synthetic time, completed-call count, task counts and
subagent lifecycle summaries, allowing progress messages grounded in runtime data.

## Limits and next steps

This is a capability interpreter over JSON state, not an OS sandbox. Live
delegation currently waits for a bounded text response; it has no child tools,
code execution or background job scheduler. Inspection tools expose retained
conversations, but launching live work in parallel, waiting and cancelling need
an asynchronous job registry. Isolated test execution needs a separate backend
with explicit resource limits and recorded evidence; adding host shell access
to this interpreter would not provide isolation.

Synthetic time still advances per tool call independently of logical token ticks.
Compiler validation enforces structure, temporal conversation availability,
artifact existence and task dependency constraints. It does not prove an authored
response is truthful, a claimed test was executed, or an acceptance criterion was
met. Recorded examples must distinguish supplied outcomes from real execution.

`examples/persistent-agents.json` demonstrates external file creation, a pinned
review artifact, independent conversations, a follow-up and runtime progress.
Its specialist responses are authored fixture data, not live execution evidence.
