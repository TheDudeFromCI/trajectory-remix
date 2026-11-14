# 00 — Existing public KTREMIX specification/reference/demo PR

Read [CONTEXT](CONTEXT.md). This is existing work, not a new overhaul PR.

## Starting state

- Public draft: https://github.com/TheDudeFromCI/trajectory-remix/pull/70
- Last local branch/head: `feat/ktremix-format-spec`, `6099ae9`.
- Files: `docs/KTREMIX.md`, `examples/ktremix_dataset.py`,
  `examples/ktremix_training_data.py`, `examples/train_ktremix.py`,
  `docs/KTREMIX-TRAINING-DEMO.md`, format/training tests and benchmark script.
- Existing demo uses the old replay compiler plus a fixture-specific adapter.
  It is NOT proof of integration with the new graph compiler yet.
- Earlier 18 format + 5 training correctness tests and historical training run
  predate the user's no-throughput-testing instruction. Do not rerun benchmarks
  or full training now, even if the old docs show those commands.

## Assignment when explicitly requested

Inspect current remote PR/main state. Preserve the fixed header/index, independent
compression, typed arrays, visibility/target/lifetime contract and uniform
multi-shard sampler unless a demonstrated correction is needed. Align public docs
with settled semantics: EOS never a context key; optional metadata; source-driven
headers/framing; Tick history is a read-only scheduling preview.

The user authorized this temporary handoff packet in #70 so new conversations
can check it out. Remove it before merge; retain the format/reference/demo artifacts.
Do not claim production Export integration already exists.

## Acceptance

- Small format/conformance tests only: offsets/counts/checksums, corrupt bounds,
  target visibility, no current-target leak, replacement/slot reuse and optional
  metadata. The one-step optimizer check is functional, not a training benchmark.
- Do not run `scripts/benchmark_ktremix.py` or full `train_ktremix.py run` here/CI.
- Handoff 02 integrates the actual new compiler and updates the demo. It may
  consume a merged PR #70 or a clearly specified dependency commit; don't silently
  duplicate/reinvent this binary format in another branch.
- Document what changed since the historical measurements; never present those
  numbers as measurements of the future graph compiler.

Deliver a narrow update to the existing draft only if assigned. Never merge.
