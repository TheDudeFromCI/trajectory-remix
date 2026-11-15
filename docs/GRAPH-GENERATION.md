# Event generation and review

The graph studio uses one proposal-driven event Flow over the existing sampler,
FIFO endpoints, source CAS and ownership services. The replay builder's global
session, executable tools and token compiler are not used. See
[local-model smoke validation](GRAPH-LOCAL-LLM.md) for the user-machine gate.

## Configure and use

Configure `endpoints` and `endpoint_roles` in Project Settings. The three role
names are `generator`, `fast`, `critical`; logical endpoint identities remain
separate even at the same URL. URLs are OpenAI-compatible **base URLs** such as
`http://127.0.0.1:8000/v1`; the transport appends `/chat/completions`. It sends a
non-streamed structured-output request, with no SDK retries or redirects.
Credentials are optional environment variables named `REMIX_API_KEY_<ENDPOINT>`;
uppercase the endpoint ID and replace hyphens with underscores. Never place
credentials in settings, instructions or source.

Project `generation` settings are `fast`, `critical`, `repair` (default true),
`format_retries` (2 additional format attempts), and `repair_context_bytes`
(65536 bytes of the serialized continuing messages). This byte budget is an
explicit transcript bound, not a tokenizer or an estimate of provider tokens.
Provider-reported context exhaustion independently ends a repair. Configure the
`chat` tool with a JSON object input containing string `content` to enable the
automatic echo helper; its tool definition has no executable implementation.
The [local example](../examples/graph_local_llm.json) supplies chat/read schemas.

Open Sampler Matrix → Proposal preview → Generate trajectories. Select pins,
seed and attempt count, then Start generation. Each attempt uses `seed + ordinal`.
Pins are request overrides: they neither edit weights nor dirty the matrix.
Save matrix/metric drafts before starting a run. Pin or settings edits after
submission do not affect any of that run's later attempts. Its job dialog shows
pending/active/detached outcome counts and manual endpoint Resume controls.

The default Flow branches on the sampled scenario: hands-off work can start
with external context; collaborative work includes another participant; relevant
tags introduce external updates; reasoning and optional chat submission follow.
It is a compact baseline, not a claim that every requested target or competency
has been achieved. Targets stay separate from measured actuals. Inspect and
iterate prompts with a local model before using outputs as training material.

## Frozen event recipes

The advanced run instructions accept `{"plan":{"windows":{...},"tasks":[...]}}`.
These are branches/recipes of the same driver, not competency-specific engines.
Every task has:

- `id`: stable event ID. Do not use IDs ending in `:echo`, reserved for helpers.
- `envelope`: intrinsic event fields except `content`, including kind, window,
  dependencies, masks, delivery, framing and closure. Dependencies reference
  preceding selected tasks. The Flow owns these fields, not the model.
- `instruction`: an event-local authoring task. Actor tasks must not contain
  privileged answers, future facts, expected verdicts or private summaries.
- `world`: explicit private facts for external authors only. Actor tasks reject
  this field when nonempty. Omit unnecessary facts even for an external author.
- `when`: optional dotted proposal paths and equality conditions. Nonmatching
  tasks are omitted; broken dependencies are rejected rather than rewired.
- `arguments`: `text` (default) returns `{"content":"..."}`; `json` returns
  `{"arguments": ...}` for a complete known tool call. Its input schema guides
  construction; mismatches remain advisory. Text mode also supports intentionally
  invalid JSON calls.
  Fragmented calls use text so incomplete JSON remains intentionally authorable.

Plans, exact proposal snapshots, project settings, checklists and instructions
are frozen before admission. The attempt keeps the sampler snapshot unchanged;
its resolved Flow is stored separately in the immutable run config. The default
recipe can be replaced by a richer explicit recipe without changing the queue,
review or repair driver. Examples are in the smoke script's `smoke_plan`.

One provider call supplies one event's content. IDs/dependencies/flags never need
to be copied by the model. Each complete event plus checkpoint commits atomically.
Prompts fold only causal ancestors into visible windows, apply replacements and
remove closed windows. Parallel/future events and metadata are excluded. The
actor receives no full proposal, hidden world data or review history. No rolling
provider history can retain lost actor context. Instructions are trusted project
configuration and must themselves respect that boundary.

A completed `chat` call with `echo_chat=true` creates a masked structured
`chat_message` from its parsable `content`, linked to the call and prior room
writers. A pending-helper checkpoint makes restart idempotent. Subsequent task
dependencies on that call resolve through its echo. `echo_chat=false` permits
filtered delivery, rejection or no delivery through explicit authored tasks.
Malformed chat arguments are advisory and produce no fabricated echo. Multiple
fragments assemble in causal order. Synthetic tools never execute; result text
is free-form, with independent begin/end, instant/streamed and closure flags.

## Reviews and continuing repairs

Editable `review_checklists.core` and `.critical` map stable criterion IDs to
`{"text":"...","allow_not_applicable":false}`. These embedded definitions are
the executable checklists; the pre-existing optional `checklists` references
remain descriptive references and are not fetched remotely. Fast covers Core;
Critical covers Core plus Critical. Signoffs preserve exact definitions/hashes,
source revision/hash, tier, operation, requested/actual model, endpoint, prompt
version, item evidence, event references, derived verdict and stage transition.

Review responses cover every expected item with nonempty evidence and valid
source event references. Unknown/missing items, unsupported not-applicable,
invalid JSON and invented event references do not create verdicts. A pass is
blocked while structural errors remain; advisory warnings do not block it.

Automatic creation moves Unfinished → Pending. Enabled Fast pass/fail yields
Candidate/Unsuccessful, and Critical after Fast success yields Validated/Rejected.
Missing review roles permit Generator-only creation; Critical never bypasses
Fast. Configured roles enable automatic use unless the project toggle is off.
A failed enabled review can enter one Repair & Validate session on that same
tier's model. A successful Fast repair may then reach the separate Critical tier.

Repair messages retain one original source/checklist snapshot plus all complete
inspection/patch exchanges until verdict or context limit. There is no compaction,
reset, second repair attempt or extra same-tier certification pass. Inspection
returns an event/causal view; patches merge into one event or window with an
expected source hash and source CAS. Full envelopes can add a missing event;
`remove_event` retains broken references for explicit correction. Accepted patches persist immediately, including editable
intermediate graph errors with diagnostics. Final passing verdicts require a
structurally valid graph. Failure/context exhaustion detaches at Unfinished,
retaining edits and any actual verdict evidence. No verdict is invented for a
context-limit outcome.

Manual Review & Repair operates on a selected saved revision or frozen matches
across all pages. Standard reviews Pending with Fast only and Candidate with
Critical; failed stages repair at their tier when enabled; other stages skip.
Force Fast/Critical ignores stage restrictions, retains higher stages on pass,
and uses normal failure transitions. Ownership is always enforced. Completion
closes the popup; closing it yourself does not stop its independent job.

Source/checklist changes derive stale status without rewriting historical
signoffs or resetting stages. Core affects Fast/Critical; Critical-only affects
Critical. Current reads expose `review_status`; export's exclude-stale option
uses the same hashes. Settings/source edits never automatically queue re-review.
The selected editor shows stale reasons after a saved read. Bulk review retains
one changed-only settled history set; Undo/Redo restores saved source results
with revision/commit guards and never invokes models.

## REST and recovery

Authenticated studio endpoints:

| Route | Action |
| --- | --- |
| `POST /api/v1/generation/runs` | `{requests:[{seed,overrides,...}],instructions:{...}}`; requires Generator; returns job reference |
| `POST /api/v1/sampler/runs` | Same generation driver when Generator is configured; otherwise preserves snapshot-only paused runs |
| `POST /api/v1/reviews` | `{query,mode,repair}` or `{trajectory,expected_revision,mode,repair}` |
| `GET /api/v1/trajectories/{id}` | Source, ownership, diagnostics and derived review staleness |
| Existing `/jobs`, `/attempts`, `/results`, `/history`, controls | Inspect progress, skipped targets, saved deltas; Pause/Resume/Cancel/detach |

`GenerationService(..., transport)` injects an async one-invocation transport.
It receives a queue `Ticket`, sends only `payload.messages`/`schema` using the
pinned `endpoint_config`, and returns `{output,model,usage?}`, `{invalid_output:
true}`, or `{context_limit:true}`. Driver-only envelope/checkpoint/provenance
fields are not provider messages. Each event/review/repair turn is a separate
FIFO item. Original payloads persist for retry; response bodies are discarded
on interruption. Provider usage is transport evidence, not a measured source metric.

Transport exhaustion pauses that endpoint, preserves stages and attached work,
and requires manual endpoint Resume. Format failures retry a bounded number of
new calls; generation exhaustion records Unsuccessful and can route to repair.
Review/repair format exhaustion pauses the job with **no quality verdict**;
manual Resume retries within the same repair session/context budget. Local
wrapper failures pause instead of asking a model to fix programmatic envelopes.
Server restart pauses jobs, fences callbacks, discards unaccepted text and waits
for manual Resume. Completed events and repair patches survive.

Cancel/detach use the existing epoch/lease fences. N attempts means N reserved
identities and detached outcomes, including failures; no automatic replacements.
Independent job workers share endpoint caps and the attached-trajectory cap.
MCP exposes `generate_graphs`, `review_graphs` and the focused `generation` guide,
using these same services. Existing `/bulk` review actions also use the queued
async driver and frozen settings.

Generic `/runs` without this driver's frozen recipe remain explicitly paused.

## Validation and limits

```bash
python -m pytest -q tests/test_graph_generation.py tests/test_graph_jobs.py \
  tests/test_graph_sampling.py tests/test_graph_studio.py tests/test_graph_export.py
python tests/run_generation_frontend.py
```

The checks use only tiny fake-provider sources and no elapsed-throughput
assertions. Existing sampler and studio browser checks remain available. Live
model quality, tool-response coherence, native-schema compatibility and local
context-limit behavior need the user-machine checks. No throughput/load/scale
benchmark, training or live LLM test is part of this suite or CI.

## Provider output contracts

Simple closed object contracts use native strict JSON-schema decoding. Repair
root unions and unrestricted tool-argument objects are not in the common strict
OpenAI schema subset. Those requests use JSON-object mode and include the full
output schema in the provider prompt. The authoritative graph schema is still
validated locally, with the same bounded format-correction policy. This changes
only the request's decoding format: the endpoint and model remain frozen, and no
provider fallback, probe or additional retry is introduced. Local servers must
support the requested JSON response format; provider errors retain normal
endpoint retry/pause behavior.
