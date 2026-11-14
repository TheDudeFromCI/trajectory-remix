# 07 — Integration acceptance, user-machine harnesses, Skills and cleanup

Read [CONTEXT](CONTEXT.md), contracts **§11–12**, prerequisite completion records
and chosen sampler UI branch. Suggested branch: `feat/overhaul-acceptance-guides`.

## Goal

Close integration gaps after 02–06; do not substitute docs for missing behavior.
The first manual milestone and actual generation pipeline must be coherent.

## Scope

- Prepare (do NOT execute here/CI) a reproducible manual workload for **25,000
  stored sources and 20 active trajectory pipelines**, with fake-provider option
  to isolate app behavior, status/log/progress correctness and user-run measurements.
- Prepare LOCAL-LLM acceptance instructions for ordinary chat, multi-user,
  autonomous wake/phase changes, tools, Fast/Critical reviews and repairs.
- Finish the real-compiler raw→compiled→sampled→training demo and manual invocation
  instructions. Historical PR #70 throughput is not a measurement of the new path.
- Update public format/source/API docs and task-oriented Skills/MCP examples.
  Agent responses should be bounded/readable and use new source IDs/contracts.
- Verify REST-only dashboard project operations, both timeline modes/debounce,
  consistent locks/diagnostics, job status bar/dialogs and artifact/cache lifecycle.
- Remove obsolete tick-anchor/sandbox/legacy paths and misleading old docs/tests
  after replacing meaningful coverage. No compatibility/migration layer.
- Keep the three matrix experiments separate until user selects one. Integrate
  only the winner; record focused UI revisions without backend divergence.

## Checks allowed here/CI

Small deterministic source/compiler/codec/API/DOM/mock-driver correctness tests.
Check metadata-free compiler inputs, profile-specific outputs, causal visibility,
framing/EOS/NOOP/replacement, CAS/fencing, FIFO/retries, frozen configs, bulk deltas,
split groups and temporary downloads with small fixtures/fake clocks.
One optimizer update may verify tensor/gradient connectivity. No full convergence,
25k seeding, 20-active stress run, latency/throughput timing suite or real LLM calls.

## Harness safety/clarity

Put manual performance harnesses outside automatic test discovery. No import
side effects or default CI performance step. Clearly mark commands USER MACHINE
ONLY; refuse CI invocation rather than silently running a scaled workload.
Never auto-start paid endpoints or download model weights while collecting tests.
Reports distinguish correctness, model verdict quality and measured performance;
include configuration/versions and actual failed/detached outcomes.

## Definition of done

- Implemented behavior matches accepted policies, not only a green smoke script.
- New compiler emits valid KTREMIX consumed by the reference adapter; no old
  replay fixture shim masquerades as integration.
- Documentation identifies local-LLM/performance checks as pending until the user
  actually runs them. No fabricated performance/quality or screenshots.
- Unsupported old formats fail clearly; no accidental persistence of removed
  sandbox/tick-marker UI or implicit compile-on-generation/metrics behavior.
- User-machine feedback is recorded and fixes are isolated/retested appropriately.

Deliver a focused draft PR and concise completion checklist with executed small
checks versus unexecuted user checks. Do not merge or mark user acceptance passed
without the user's actual results.
