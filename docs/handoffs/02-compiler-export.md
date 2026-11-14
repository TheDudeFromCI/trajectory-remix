# 02 — Graph scheduler, real compiler and temporary export artifacts

Read [CONTEXT](CONTEXT.md), contracts **§2, §4, §8, §10** and handoff 00.
Requires 01 and the public format/reference contract from PR #70. Suggested
branch: `feat/graph-compiler-export`.

## Inspect first

`compiler.py` (especially `visible_to`, row construction and headers), `training.py`,
`tools/_protocol.py`, public `docs/KTREMIX.md`, reference sampler/writer/demo and
tests for headers, window references, masking, slot limits and replacements.
Preserve training semantics, not old source readers or executable tools.

## Scope

- One scheduler shared by full export and opt-in read-only Tick history. Export
  emits arrays/artifacts; preview emits a bounded visual schedule only.
- Independent per-event tokenization; logical controls emitted from flags, never
  discovered by scanning token placeholders. Handle literal control-looking data
  without accidentally creating lifecycle control tokens.
- Source-dependent header/framing timing, same-input-phase external chains and
  optional next-input-tick checkbox; agent dependencies begin next tick.
- Progressive Available binding, deterministic agent-opening staggering, randomized
  reusable physical slots, EOS closure and capacity excluding Available.
- Explicit causal frontiers/target rows/visibility; no own or simultaneous target
  leakage, no EOS KV key, no closed/replaced content resurrected through hidden
  states. Replacement clears and inserts first token atomically.
- Implicit NOOP and sleep compression; never invent authored wait durations.
- Raw JSONL and compiled KTREMIX, split-specific shards, full trajectories per
  file. Metadata projection never removes compiler inputs.
- Resolve Auto/missing split before trimming; default include missing, optional
  exclude. Account for explicit counts and indivisible known families, block
  conflicting explicit family splits, persist source metadata unchanged.
- All shards/manifests/ZIPs in unique job-owned **/tmp** paths; complete artifacts
  only, partial cleanup and 24h completed expiry. Individual downloads plus ZIP.
- Pure compiler/artifact services can land before 05; wire asynchronous REST
  export jobs to the real job service, not a fake production placeholder.

## Training-demo integration

Replace the old replay-plus-adapter path with actual new source/compiler output.
Keep about 50k parameters, two layers, shared global KV, 36 ordinary letters/digits,
three Active windows plus Available, masked external inputs, earliest eligible
modulo-26 outputs and EOS. Framing may require revising special-token vocabulary/
parameter accounting; don't bypass required protocol rules just to keep an old
assertion at exactly 49,968 parameters. Header timing and variable input arrivals
need explicit fixture accounting. Do not reintroduce generic authored tick delays
or claim a fixture-only adapter is the real compiler. Raise any genuine conflict
with a concrete example before silently changing these requirements.

Prepare the updated training commands/harness; **the user runs full training and
throughput on their computer**. A tiny array/one-step optimizer test is allowed.

## Required small checks

Fixtures: agent/external header timing; external→agent same tick; agent→dependent
next tick; both external-chain checkbox states; same-window collision errors;
Available activation/staggering/reuse; EOS same-pass pre-content/next-pass absence;
replacement/masks/event token boundaries; all-NOOP sleep; instant/streamed results.
Also check metadata-free output arrays, tokenizer-profile independence, split
shards, CRC/bounds/atomic temporary files and cancellation/expiry with fake clocks.

Use a few records only, no timed sampling or compiler throughput experiment.
Reference old attention predicates as regression oracles without exposing old
formats in the new app. Finish with a focused draft PR, fixture evidence and a
clear list of user-machine training checks not run. Do not merge.
