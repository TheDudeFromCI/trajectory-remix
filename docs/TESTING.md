# Verification

- Dataset metric regressions cover compiler token/control accounting, actual
  concurrent generation versus open windows, streaming versus instant input,
  canonical same-tick/replacement/closure visibility, tool invocation and latency
  counts, nullable denominators, fixed/rebinned distributions, pooled units,
  accepted/train/original population isolation, evidence-backed labels and unknown
  denominators, family counts, split conflicts and exact replay candidates.
  A SQLite authorizer rejects replay source reads during cached queries. Cache
  checks cover reopen reuse, pre-feature v5 files, source/tool/tokenizer/definition
  invalidation, concurrent edits during a blocked compilation, nonblocking API
  reads, deletion cascades and metadata undo. MCP protocol discovery/calls use the
  official SDK. DOM checks exercise View access, calculation, graphs, population
  changes, repair links, structured provenance/label editing and nested metadata
  preservation; the metrics panel and replay sidebar were also inspected in
  headless Chromium.

- Python tests run with `python -m pytest -q`. Install `.[mcp,test]` to also
  exercise official-client MCP protocol tests and the stdio bridge; MCP tests
  are skipped when the optional SDK is absent. Authoring tests cover atomic
  edits, revision conflicts, patches, metadata, diagnostics and timeline inspection.
- Scenario checklist tests cover exact rule coverage, typed statuses, evidence,
  applicability, code-derived acceptance/findings, missing-context handling,
  bounded cross-view evidence requests, saved group reuse, and complete reviews
  after targeted repair. Legacy single-prompt/string/object checks remain covered.
- Repair recovery regressions cover malformed JSON/schema, review-wrapper edits,
  invalid source pointers, edit conflicts, compilation failures, atomic retention,
  bounded retry exhaustion, full-replay repairs, and repair budgets across provider
  interruption/resume. Review regressions cover malformed/unknown/repeated context
  requests, exhausted rounds, narrowing over-budget evidence requests, and batch
  pause/resume with Pending rather than rejected drafts. Lifecycle checks verify
  tool evidence survives reasoning and retained reasoning survives chat delivery;
  the dashboard test resumes evaluation failures without starting a fresh sample.
- MCP scenario reviews cover scenario-tag and legacy-metadata resolution,
  preservation of randomized tags, portable checklist snapshots, exact results
  schemas, incomplete/stale review rejection, atomic dry-run/undoable repairs,
  API-free per-item recording, and transport tool discovery.
- Guidance tests verify identical dashboard/MCP bundles, unchanged read-only standard
  files, persistent dataset/scenario Markdown, portable saved prompts, custom-check
  result coverage and stale checklist rejection. DOM checks edit both scopes, switch
  guide tabs and scenarios, view exact source, and render user Markdown as safe text.
- Streamable HTTP checks cover direct POST initialization, initialized notifications,
  tool discovery/calls, session and protocol headers, deletion, malformed requests,
  HTTP methods, CORS preflight/exposed session headers and explicit origin/Host
  restrictions without weakening dashboard token checks. A real network test uses
  the official SDK, verifies HTTP and stdio share edits, and holds GET/SSE open
  while POST remains operational. The dashboard also works without the MCP extra.
- DOM integration checks run with `python tests/run_frontend.py` after
  installing the optional `tests/package.json` dependencies. They execute the
  dashboard JavaScript against a temporary local backend and exercise sample
  loading, playback controls, event and JSON editing, marker diagnostics and
  task/endpoint/condition/automatic configuration surfaces, external MCP edits,
  and protection of pending browser edits during conflicts.
  Large-preview checks compile over 50,000 rows from little source text, seek
  forward/backward without fetching a row prefix, cross streamed-output and
  content-replacement boundaries, buffer playback during a delayed range read,
  and handle range-output limits and expired IDs without retry loops.
  Python regressions compare indexed frames with canonical replay across EOS,
  pending windows, sandbox completion and failed tool outcomes; verify MCP
  range previews and paginated NOOP inspection; preserve loss/attention/export
  records; and exercise cache reuse, row budgets, LRU eviction, idle expiry,
  separate compilation/preview status, and unchanged compiler safety limits.
  They also exercise dataset Undo/Redo menu actions, keyboard shortcuts, and
  undoing a pending draft after it is persisted.
  Sandbox checks author a delayed file write, inspect its running duration, and
  scrub forward/backward across completion without displaying future file state.
- Sandbox replay tests cover completion ordering, all virtual stores, literal
  marker/control-token data, offline subagent outcomes, edit recomputation,
  generation continuity, and masked result visibility in token exports. MCP
  checks schedule calls through the official stdio client and shared dashboard,
  including source history, revision conflicts, atomic batches and persistence.
  Parallel tool tests pause a streamed request with supervised NOOPs, run the
  clock in another window, and verify the resumed request can attend to its
  result without seeing the pending write outcome. They also cover tick-0 BOS,
  reusable tool windows, request anchors, segment losses and dispatch timing.
  Requests always generate one token per tick; result checks cover instant and
  streamed delivery, rates, masks, final-token anchors, dependent execution order,
  duplicate/cyclic markers and collision-free, undoable anchor allocation. DOM
  checks exercise result delivery and persistence of the post-result anchor toggle.
- Persistent sandbox tests cover invisible mutations without token/window/export
  changes, history beyond the token horizon, author diagnostics for hidden failures,
  authored follow-up message retention, isolated conversations, restored generation,
  dispatch-time resume checks, pinned artifacts, stale writes, quotas, task
  dependencies and runtime status without future answers. MCP and DOM tests
  cover external event authoring, undo/redo, persistence and historical scrubbing.
  Synthetic subagent tests reject malformed replies before dispatch, preserve
  failed identities for clarification, and forbid nested provider calls. The
  bundled Skill.md is available through MCP and its worked call sequence is
  tested for parallel timing, follow-ups, external writes and masked observations.
  Short guide checks execute the published JSON calls with current revisions and
  configured EOS tokens: first-trajectory closure and anchors, local text/timing
  repairs, concurrent specialist execution without future replies, and invisible
  file setup followed by a visible read. Guide retrieval defaults to a short
  starting page; explicit topics and the full-reference option use the same MCP
  handler and reject unknown topics.
- Chat checks cover message framing, metadata speaker names, missing/closed
  windows, completion-time lifecycle, literal arguments, exported loss masks,
  generation error feedback and undoable MCP recording. DOM checks cover the
  sidebar metadata editor, nested-property preservation, Source JSON sync,
  metadata Undo/Redo, invalid metadata JSON and selecting the chat tool.
- SQLite tests cover concurrent writers, stale edits, rollback, snapshot reads,
  JSON/JSONL imports, and atomic CLI export. A 100,005-trajectory test forbids
  full dataset iteration and checks bounded Python allocations during startup,
  polling, single edits, batches and checkpoints. Project initialization tests
  verify embedded tokenizer/special-token round trips, launching after deleting
  the tokenizer source file, refusing reinitialization and launch-time tokenizer
  overrides, and rejecting missing databases without creating files.
- Tokenizer migration checks cover configuration validation, every saved source
  being recompiled, live progress, generation/edit locks, concurrent revision
  conflicts, atomic rollback, new token-boundary failures, custom special tokens,
  empty datasets, persisted tool-plan replacement, preview invalidation and
  restart persistence. DOM checks exercise the File menu dialog, pasted JSON,
  uploads, inline errors, disabled inputs, completion and source preservation.
- Window header/reference checks cover initialization order, loss masks, same-tick
  visibility, closure, implicit/repeated tool calls, native tokenizer IDs, stable
  numeric slots, multi-digit references, anchors, segment masks, literal tool
  data, unknown markers, export round trips, task layouts and undoable renames.
- UUID/metadata checks cover immutable identity, duplicate UUID rejection, seeded
  slot allocation and implicit-window budgets, typed key filters, tag membership,
  grouped counts, pagination without source reads, transactional projections,
  content hashes, undo/redo, cascading deletion and rollback.
- Search checks cover readable replay text, case folding, AND/OR precedence,
  nested negation, word wildcards, property comparisons, tool usage and tags,
  matching SQL/draft behavior, pagination, invalid syntax, migration rollback,
  and transactional updates through edits/imports/undo/redo. MCP protocol checks
  retrieve the packaged search guide and exercise queries and errors. DOM checks
  combine operators beyond page one and verify inline query error recovery.
- Compiler diagnostic search checks cover `@errors`/`@warnings` true and false,
  SQL/draft parity, invalid boolean values, grouping/negation, filtering before
  pagination, lazy cache reuse, source/tool invalidation, reopening, and dashboard/MCP
  agreement through edits and undo/redo without changing pinned dependencies.
- A Python wheel builds successfully and contains all dashboard assets.

LLM integrations are tested against deterministic fake completions and an
HTTPX mocked OpenAI-compatible transport. No live provider credentials were
available, so actual model/provider behavior is unverified. The metadata sidebar
layout and chat message display were also inspected in headless Chromium. The
DOM checks use an emulated browser and do not replace full interactive browser QA.

Re-run the tests when changing compiler visibility, stream timing, tokenization,
window closure, tool capabilities or generation task assembly. The reactive
fixture keeps the tool window open until the final response has consumed its
result, then emits EOS to release it.
