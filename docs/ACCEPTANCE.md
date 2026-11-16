# Integration and user-machine acceptance

Workbench B is the selected sampler UI. Integration is on
`feat/overhaul-acceptance-guides`, based on `feat/event-generation-review` at
`56bbb153936b8e25b495aea3f959197405ce8048`. The draft targets that branch.
The user has delegated completion, review and merging to the coordinator and
waived stress testing. A green deterministic check or synthetic model verdict
still does not establish content quality or training convergence.

## Dashboard restoration and final review

The desktop workflow runs over the graph REST services: Editor, Jobs, Metrics and
Sampler views; a dataset panel, central windows/timeline and a tabbed event editor.
Settings has endpoint/role/tokenizer/tool forms; Generate, the Review Settings tab and the
Jobs view are persistent actions. Clean sources update live and unlock on completion;
real unsaved drafts survive changes. See [Dashboard](DASHBOARD.md) and
[Project Settings](PROJECT-SETTINGS.md).

A fresh-project Chromium check exercises form setup, endpoint/tool draft round
trips, generation, job pause/resume, clean live updates, historical scrubbing and
dirty-buffer retention. Screenshots are in `docs/images/dashboard-restored/`.
The independent audit additionally fixed ambiguous REST export option types,
missing default chat schemas, and strict-provider response-schema compatibility.

Real OpenAI and Hugging Face connectivity attempts were blocked by the execution
network proxy with tunnel HTTP 403 before a model response. No live external-model
quality claim is made. The full 3,000-step demo check passed with the corrected arithmetic-only metric:
99.8828125% held-out sum accuracy and 64/64 exact free-running trajectories.
The 50,208-parameter two-layer shared-KV model uses compact framing tokens and
an ordinary positional attention prior; compiler masks are unchanged. The original
header-contaminated metric is fixed. See [training demo](KTREMIX-TRAINING-DEMO.md) and
[validation results](VALIDATION-RESULTS.md) for the command, evidence and limitations.
Scale/stress workloads were not run and are no longer a release gate under the
current user instruction. The manual workload harness remains available for later
use. Historical evidence and previously prepared commands follow below.


### Integration-review follow-up — November 14, 2026

The targeted follow-up starts exactly at integration commit
`110fc26c6653966374fc8c5cae244236fb22c829` and targets
`feat/overhaul-acceptance-guides` in a separate draft PR.

- Manual review/repair detach now atomically settles the bulk item/progress,
  preserves committed changed-only Undo history, releases ownership and fences
  later results. Other items continue; detaching the last item completes the job,
  including while paused. No quality verdict is invented.
- Windows have an `intentionally_left_open` acknowledgment and dashboard control.
  It suppresses only that window's `open_window` advisory, preserves existing
  default source hashes and leaves compiled execution/lifecycle errors unchanged.
- Demo arrivals now depend only on external events. The prior result/header gates
  were removed. The streamed-header conflict was explained before changing the
  agreed timing criterion: the user selected streamed headers, independent
  arrivals and sums at the earliest legal tick after the header/prior output.
  [Demo timing](KTREMIX-TRAINING-DEMO.md) documents the rule and its limits.
  Reports distinguish input-ready ticks from legal emission ticks; `--check`
  rejects old result-gated datasets even with perfect nominal predictions.

Focused regressions cover these contracts, including interrupted repairs,
remaining work, Undo, per-window diagnostics, unchanged compiler arrays and
external-schedule invariance after removing agent events. Current validation
results are recorded in the follow-up PR. No benchmark, load/scale workload,
optimizer update, full training, live LLM call or merge was performed in this
follow-up. User-machine acceptance remains pending.

## Prerequisite evidence

These completion records were read before the integration audit. Their checks
are historical evidence; current executed checks are recorded separately below.

| Package | Integrated head | Completion record |
| --- | --- | --- |
| Graph source/CAS | `f11f400` | [#71](https://github.com/TheDudeFromCI/trajectory-remix/pull/71) |
| Real compiler/KTREMIX/demo | `2192e08` | [#72](https://github.com/TheDudeFromCI/trajectory-remix/pull/72) |
| Jobs/FIFO/recovery/bulk | `81570e0` | [#73](https://github.com/TheDudeFromCI/trajectory-remix/pull/73) |
| Manual studio/REST/MCP | `55b32e8` | [#74](https://github.com/TheDudeFromCI/trajectory-remix/pull/74) |
| Exact sampler/metrics | `2e91fff` | [#75](https://github.com/TheDudeFromCI/trajectory-remix/pull/75) |
| Selected Workbench B/pins | `ce7322c` | [#79](https://github.com/TheDudeFromCI/trajectory-remix/pull/79) |
| Event generation/review/repair | `56bbb15` | [#81](https://github.com/TheDudeFromCI/trajectory-remix/pull/81) |

UI A/C remain independent design branches; this integration changes neither.

## Audit and integration changes

| Surface | Actual behavior and integration result |
| --- | --- |
| Startup/import/source | Graph-only CLI/application factory. Old databases fail clearly; invalid/old import records skip with logs. No migration, sandbox or tick anchors. |
| Manual authoring | REST shared commands, revision guards and ownership. Broken references stay editable. Save/delete controls reflect saved lock ownership; server guards remain authoritative. |
| Diagnostics | Current reads use current tool schemas; explicit historical reads retain pinned diagnostics. Changed schemas no longer leave the selected read with old warnings. |
| Timeline/compiler | Graph default needs no tokenizer; opt-in Tick history uses the real scheduler, trailing debounce and stale-response fencing. Profile selection for preview/export is independent. |
| Workbench B | Copy/remove entries and add/copy/remove rules now have draft controls. Known dangling constraints, duplicate IDs and all-zero groups reject Save. Pins/references are not silently rewritten. |
| Project tools | Object input-schema creation/field/required controls save through settings CAS. Nested/custom constraints and non-object schemas use Advanced JSON. No executable implementation or output schema. |
| Generation/reviews | Frozen proposal/config/checklist inputs, causal actor context, FIFO/retries, attached/manual ownership, continuing repairs and explicit recovery use the shared driver. No implicit compilation. |
| Jobs/logs/artifacts | Independent dialogs/status, compact polling, changed-only settled bulk history, per-job logs, cap/clear/export, fenced publication, leased `/tmp` downloads and fake-clock 24-hour expiry coverage. |
| MCP | Shared HTTP/stdio services; bounded status/session/outcome/log responses, lifecycle controls and artifact paths. Full source read is explicit; run configs/checkpoints/proposals are omitted from routine replies. |
| Training path | Real graph → compiler → KTREMIX → reference sampler → shared-KV model. Tiny fixture verifies one optimizer update; full training remains user-owned. |

The obsolete replay runtime, dashboard, tools, flows, examples and their tests
were removed together. Graph/codec tests replace meaningful source, causal,
framing, mask, lifecycle, CAS, job and transport coverage. The historical phase
visibility predicate survives only as a test oracle; schema projection regression
cases survive independently. Old performance claims and entry guides were removed.

## Executed correctness checks

Only small deterministic checks were run for this integration:

- Graph source/compiler/export/jobs/sampler/generation/studio/decoding/entry tests:
  167 passed, with 14 subtests passed, plus 1 passing real HTTP/stdio transport
  regression. Three harness guard/import/CLI checks also passed after final reporting edits.
- Real compiler/training fixture: 5 passed, **exactly one optimizer update** total.
- Graph DOM/API, Workbench B Chromium, and generation/review Chromium checks
  passed against temporary projects and an injected fake provider where needed.
  These include structure edits, pins/frozen runs, tool-schema forms, both timeline
  modes, debounce and conflict retention. No screenshot or performance result is
  fabricated; ordinary tests do not rewrite screenshots.

Package assets, CLI/guard checks and final whitespace/link checks are listed in
the PR. No benchmarks, 25k seeding, 20-active run, full training or live LLM calls
were executed by the integration agent. Never rerun the optimizer fixture in a
session with a one-update limit.

## USER MACHINE ONLY: stored/active workload

Install the project, then explicitly run in a **fresh directory**:

```bash
python -m pip install -e '.[mcp,export]'
python scripts/graph_workload.py --user-machine --provider fake \
  --stored 25000 --active 20 --attempts 40 --seed 137 \
  --output /tmp/remix-workload-fake-01 --serve
```

This command is prepared, **not executed here**. It imports 25,000 tiny sources
in bounded REST batches and submits 40 reserved attempts through the actual
queued event/review pipeline, with an attached cap of 20. The in-process fake
provider replaces all network endpoints. Its fixed observations/verdicts do not
measure model quality; it never executes tools or starts a model server.
The app uses the ordinary default `/tmp` artifact registry and durable database.

Open the printed loopback URL while it runs. Check pagination/search and a retained
editor draft, both modes (Tick history needs your configured compatible profile),
independent job dialogs, progress/outcomes/logs, and a raw export/log download.
After workload completion, `--serve` retains the studio until Ctrl+C. For pause,
endpoint failure/restart/cancel experiments use a separate copy of the project;
Resume is always manual. Observe skipped/conflicting bulk items and Undo/Redo
changed deltas. Do not erase failed/detached outcomes or fill their quota anew.

`report.json` identifies commit, Python/platform, provider/models/caps/retries,
counts/seed, timestamped job progress and observed attached count, actual outcomes,
retained log usage and elapsed wall time. It labels acceptance false and quality
unassessed. `active_target_observed=false` means the sampling interval did not
establish 20 active; it is not a successful concurrency measurement. Reduce
`--poll` or observe durable attempts/owners before claiming that gate passed.
Wall time includes import, orchestration and polling, not isolated model latency.
Use your own OS/device telemetry for CPU, RAM, VRAM and disk measurements and
record the tool/version/measurement interval alongside this report.

For your already-running local LLM, use a fresh directory and explicit opt-in:

```bash
python scripts/graph_workload.py --user-machine --provider local \
  --config /path/to/local-config.json --stored 25000 --active 20 --attempts 40 \
  --output /tmp/remix-workload-local-01 --serve
```

Only do that after the small quality cases below. It uses exactly your configured
URLs/models/roles and transport retry budgets. No hosted URL, model weights,
automatic endpoint launch, fallback, probe or auto-Resume is supplied. Choose
endpoint concurrency appropriate to your machine. Pauses stop workload submission
and preserve the project/report for manual recovery; they are not quality failures.
All manual workload CLIs require `--user-machine` and refuse known CI environments;
importing modules, collecting tests or requesting help does not run the workload.

## USER MACHINE ONLY: model quality and training

Run [local-LLM cases](GRAPH-LOCAL-LLM.md) for ordinary chat, multiple participants,
autonomous wake/phase transitions, partial tool calls/results, instant/streamed
responses, replacement, filtered/rejected echoes, Fast/Critical reviews, continuing
repairs, context limits and explicit failure/restart recovery. Inspect actual
samples and per-criterion evidence; schema success and model passes are separate
from human assessment. Reports identify commit, config/model roles, cases/seed,
actual stages/outcomes and model evaluations. They do not measure performance.

Then run the [real-compiler training demo](KTREMIX-TRAINING-DEMO.md) explicitly on
your machine. Record configuration/versions, held-out accuracy, teacher-free
timing/closure, device memory and measured throughput. The prior replay demo's
published results are not evidence for the new framed task. None of these gates
has passed until you supply actual user-machine results and acceptance decisions.

## Remaining scope and merge hygiene

Friendly controls now cover entries inside existing groups (including families,
behaviors, variants, event tags), plus simple conditional/forced rules. **New
top-level dimension groups and decision axes, arbitrary nested/multi-condition
rule editing and cross-group spreadsheet paste do not have dedicated friendly
controls.** They remain Advanced JSON work. This is unfinished convenience scope,
not evidence of an implemented arbitrary visual rule editor. Custom constraint
satisfiability is still the supplied sampler's responsibility; its original
unmatched `request_approval` condition is preserved unchanged.

Other explicit limits: the browser shows the first 1,000 preview frames, imports
are bounded to 40 MB, the status bar lists the newest 50 jobs, native Windows
server/file leases are unvalidated, and default generation prompts need local
quality iteration. Export-only metrics are not automatically written back as
source observations. No unrequested semantic tagging service was added.

After the bounded integration checks completed, the tracked temporary packet
(`docs/handoffs/`, `docs/IMPLEMENTATION-CONTRACTS.md`,
`docs/ARCHITECTURE-OVERHAUL.md`) was removed in this integration branch, before
merge. Copies remain under the checkout's ignored `work/planning-packet/` for
review, and the original tracked versions remain recoverable from the exact
base commit above and [draft #70](https://github.com/TheDudeFromCI/trajectory-remix/pull/70).
Public relative links are checked after removal. The current product contracts,
validation commands, outstanding gates and this cleanup record remain in public
documentation. Draft only: do not merge or declare user acceptance passed.
