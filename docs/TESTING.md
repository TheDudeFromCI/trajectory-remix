# Verification

- Python tests run with `python -m pytest -q`. Install `.[mcp,test]` to also
  exercise official-client MCP protocol tests and the stdio bridge; MCP tests
  are skipped when the optional SDK is absent. Authoring tests cover atomic
  edits, revision conflicts, patches, metadata, diagnostics and timeline inspection.
- DOM integration checks run with `python tests/run_frontend.py` after
  installing the optional `tests/package.json` dependencies. They execute the
  dashboard JavaScript against a temporary local backend and exercise sample
  loading, playback controls, event and JSON editing, marker diagnostics and
  task/endpoint/condition/automatic configuration surfaces, external MCP edits,
  and protection of pending browser edits during conflicts.
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
- Chat checks cover message framing, metadata speaker names, missing/closed
  windows, completion-time lifecycle, literal arguments, exported loss masks,
  generation error feedback and undoable MCP recording. DOM checks cover the
  sidebar metadata editor, nested-property preservation, Source JSON sync,
  metadata Undo/Redo, invalid metadata JSON and selecting the chat tool.
- CLI export succeeds on the included reactive sample.
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
