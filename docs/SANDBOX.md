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
keeps the originally supplied artifact context on later turns. Blocking text-only
calls embed bodies in that context. Background calls retain references and load
the pinned files into the private workspace, where workers read them with tools.

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

## Background work and live recording

`subagent({"task":"...","background":true})` returns a queued `jobId` and
`conversationId`. Configured workers run concurrently, each with a private
workspace, scoped artifact inputs and bounded tools. External workers can claim
jobs through MCP instead of using provider credentials. A conversation keeps
its external worker identity and private workspace across follow-ups. Native
workers pin their broker when dispatched. Nested delegation is unavailable.

`subagent_result`, `subagent_wait` and `subagent_cancel` accept `jobId`. Wait
releases the parent session lock, with a maximum 30-second wait per call.
Cancellation stops active work, including an in-flight execution, and prevents
late results from publishing. Job states are queued, running, completed, failed
and cancelled. `elapsed_ms` measures dispatch through completion, including queue
time, without pretending token ticks are elapsed wall time.

Bind `create_tool_session(trajectory_id="sample-id", ...)` to a compiled source
trajectory. Root calls become executable events in one shared tool window.
Worker transitions, file changes and receipts become invisible source events;
masked completion observations deliver responses/references to Kidra. Worker
publication checks file versions and records one atomic source edit. A conflict
cannot silently overwrite another worker's changes. Source edits outside the
binding are detected; restore the expected source or resolve recorded jobs and
bind a new session. Source undo does not undo external work.

The registry retains up to 64 jobs and 16 private workspaces per session. Each
child has at most 512 KB state and 128 calls across its conversation. Configured
workers additionally have 32 rounds/64 tool calls per turn and at most eight
concurrent worker requests. The coordinator retains its existing 2 MB state and
4 MB call-log limits. Active workers are cancelled on session deletion/shutdown.
Private child workspaces are ephemeral; saved source retains conversations,
published artifacts, receipts and job outcomes. Active jobs are not automatically
restarted after a server restart. Resolve/cancel unfinished recorded jobs before
binding a saved trajectory for more work.

## Isolated execution and evidence

Start with `--execution-backend docker` and an installed Python image, then opt
the session into `enable_execution: true`. `python_run` executes an argv array
against immutable virtual files. It uses a pinned image ID, UID 65534, no network,
read-only root/inputs, dropped capabilities, no privilege escalation, 128 MB RAM,
one CPU, 64 processes, bounded temporary storage and bounded output. Timeout and
cancellation remove the container. No host interpreter fallback exists. Docker
shares the host kernel; it is not a microVM or a general hostile-code guarantee.

Receipts contain exact file versions, argv, image ID, exit status, timeout and
truncation flags, output, elapsed time and a checksum. `execution_get` reads them.
`note_save(requires_evidence=true, evidence=[...])` requires passing executions
against current inputs before marking a task done. Code changes invalidate stale
evidence. Receipts are reconstructed offline; compiler inspection never executes
Docker. The checksum detects inconsistency, not malicious re-authoring.

`metadata.coordination_contract` makes validation check specialist and worker
counts, retained follow-ups, completed TODOs, current passing evidence, actual
artifact context supplied to reviewers, progress calls and coordinator file-write
constraints. `finalize_trajectory` closes all open windows in one EOS batch after
jobs and notes finish. Large dashboard previews omit token rows from responses
while retaining server-side frame replay.

## Limits

Synthetic time still advances per tool call independently of logical token ticks.
Compiler validation enforces structure, temporal conversation availability,
artifact existence, receipt consistency and task dependency constraints. It
cannot prove arbitrary prose assertions or establish who authored an edited
source file. A live run supplies stronger evidence than an authored fixture.

`examples/persistent-agents.json` demonstrates external file creation, a pinned
review artifact, independent conversations, a follow-up and runtime progress.
Its specialist responses are authored fixture data, not live execution evidence.
The separate [six-specialist benchmark](BENCHMARK.md) records an actual MCP run
with six external workers and Docker execution.
