# Kidra / Trajectory Remix implementation handoffs

> **Temporary handoff material in draft PR #70. Remove `docs/handoffs/`,
> `docs/IMPLEMENTATION-CONTRACTS.md`, and `docs/ARCHITECTURE-OVERHAUL.md` before merge.**

Use these in fresh conversations. The user approved the consolidated plan and
then requested handoffs instead of continuing implementation in the long design
conversation. **No overhaul implementation code has been changed yet.**

## Start here

Paste this into a new conversation, substituting the assigned handoff:

> Read `docs/handoffs/CONTEXT.md` and `docs/handoffs/01-graph-foundation.md`.
> Implement only that handoff, using the approved contracts and prerequisite
> work. Run small correctness tests, open a focused draft PR, and report what
> remains. Do not merge or run throughput/load/training-performance tests or
> live LLM calls here or in CI.

The numbered labels below are work packages, **not GitHub PR numbers**. Verify
the remote repository/branches before starting; prerequisite changes may have
landed since this packet was written.

| Handoff | Branch suggestion | Prerequisites |
|---|---|---|
| [00 Existing format/demo PR](00-existing-format-pr.md) | existing `feat/ktremix-format-spec` | Inspect public PR #70 |
| [01 Graph foundation](01-graph-foundation.md) | `feat/graph-source-contracts` | None |
| [02 Compiler/export](02-compiler-export.md) | `feat/graph-compiler-export` | 01; format/reference changes from #70 |
| [03 Manual studio/API](03-manual-studio-api.md) | `feat/graph-studio-rest` | 01, 02, backend job primitives from 05 |
| [04 Sampler/metrics](04-sampler-metrics.md) | `feat/sampler-metrics-registry` | 01; restore exact supplied sampler files |
| [05 Queue/recovery/bulk](05-queue-recovery-bulk.md) | `feat/job-queue-recovery-bulk` | 01; coordinate artifact interfaces with 02 |
| [06 Generation/review](06-generation-review.md) | `feat/event-generation-review` | 01, 04, 05 |
| [07 Acceptance/cleanup](07-acceptance-cleanup.md) | `feat/overhaul-acceptance-guides` | Integrated 02–06 and chosen matrix UI |
| [UI A Guided](ui-a-sampler-guided.md) | `design/sampler-guided` | 03 + shared 04 backend |
| [UI B Workbench](ui-b-sampler-workbench.md) | `design/sampler-workbench` | Same |
| [UI C Scenario](ui-c-sampler-scenario.md) | `design/sampler-scenario` | Same |

Suggested critical path: **01 → 02 + 05 → 03**, the first usable manual
authoring/export milestone. Then 04 and the three UI experiments; 06 requires
durable ownership/FIFO from 05 before enabling real parallel generation. Finish
with 07. Independent foundation work may be developed concurrently in isolated
branches only when assigned; no instruction here authorizes spawning agents.

## Reading without loading the whole conversation

1. Read [CONTEXT.md](CONTEXT.md), the short stable common policy.
2. Read the assigned handoff, including its boundary/acceptance checks.
3. Read only the named sections of
   [IMPLEMENTATION-CONTRACTS.md](../IMPLEMENTATION-CONTRACTS.md).
4. Consult the long [decision register](../ARCHITECTURE-OVERHAUL.md) or the existing
   Notion project memory only for an actual ambiguity. Do not re-ask settled policy.

Concrete field names in the contract draft are engineering proposals the user
approved as a baseline. Finalize them in 01 and update downstream handoffs if a
technical correction is necessary. Do not change accepted product behavior to
make an implementation shortcut work.

## Workspace / public repository boundary

- Repository: `TheDudeFromCI/trajectory-remix` (public).
- Packet branch: `feat/ktremix-format-spec`, public draft PR #70.
- Graph implementation has not started. The packet is temporarily tracked on
  that branch, so a fresh checkout contains all handoffs and supporting plans.
- Fetch/check out that branch first, then read the assigned handoff and create
  its suggested implementation branch. Verify prerequisite branches before work.
- Remove the temporary packet before merging #70. Follow-up implementation PRs
  should contain concise product documentation rather than the full design history.
- Never merge a PR merely because a handoff asks for a draft PR.

## Known input dependency

The original `kidra_sampler.py`, `kidra_sampling_matrix.v1.json` and
`kidra_example_definition.json` were inspected earlier, but their physical copies
are not currently in this workspace. Exact hashes are in handoff 04. Restore
them before the adapter work; do not reconstruct the sampler from summaries.
This does not block 01/02/05 or the manual studio milestone.

## User-owned validation

Target: **25,000 stored trajectories, 20 active pipelines**. Throughput/load and
full training experiments run **only on the user's machine**, never here or CI.
The user also runs live acceptance with a **local LLM**. Author harnesses and
instructions; do not execute those workloads. Small functional/mock correctness
tests and a one-step optimizer test are allowed.

## Completion record

At the end of an assigned work package, record its actual branch/head, draft PR,
changed public contracts, small checks run, unrun user-machine checks, and any
prerequisite supplied to the next handoff. Keep the user's existing Notion project
memory current according to their current instructions; do not copy code there.
