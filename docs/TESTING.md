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
- CLI export succeeds on the included reactive sample.
- A Python wheel builds successfully and contains all dashboard assets.

LLM integrations are tested against deterministic fake completions and an
HTTPX mocked OpenAI-compatible transport. No live provider credentials were
available, so actual model/provider behavior is unverified. Visual inspection
in a real browser was blocked by the execution environment's network separation
and unavailable browser binary; the DOM checks are not visual browser QA.

Re-run the tests when changing compiler visibility, stream timing, tokenization,
window closure, tool capabilities or generation task assembly. The reactive
fixture keeps the tool window open until the final response has consumed its
result, then emits EOS to release it.
