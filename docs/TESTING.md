# Verification

- 74 Python tests pass with `python -m pytest -q`.
- 12 DOM integration checks pass with `python tests/run_frontend.py` after
  installing the optional `tests/package.json` dependencies. They execute the
  dashboard JavaScript against a temporary local backend and exercise sample
  loading, playback controls, event and JSON editing, marker diagnostics and
  task/endpoint/condition/automatic configuration surfaces.
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
