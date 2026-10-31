# Six-agent Trajectory Remix experiment

Conducted October 28, 2026, 11:47 AM MST. Application source: `791c28a1db95152957924e9e1a1d0b68008a30a0`. Six fresh worker agents, one trajectory attempt each: three `user_problem` and three `user_scenario_noval`. Workers were not told method labels, pairing, or the contents of other trajectories. They received only their own queued API messages and response mechanics. All answers came from these workers; the proxy supplied no canned semantic responses.

**Result:** all six attempts retained complete, compilable, semantically correct task drafts in this review. The new method used **39 → 15 calls (61.5% fewer)**. Mean final-draft creation time fell **96.1 → 67.4 seconds (29.9%)**. Mean end-to-end time fell **177.9 → 67.9 seconds (61.8%)**, but the old runs stopped at review-context failures, so that last comparison is time to termination, not time to successful validation.

## Observations

| Case | Model calls | Final draft ready | Attempt ends | User turns | Review score | Pipeline status |
|---|---:|---:|---:|---:|---:|---|
| pair1-old | 13 | 70.2 s | 165.3 s | 1 | 16/16 | evaluation_failed |
| pair1-new | 4 | 40.3 s | 40.7 s | 1 | 16/16 | complete |
| pair2-old | 13 | 128.5 s | 197.8 s | 1 | 15/16 | evaluation_failed |
| pair2-new | 4 | 66.6 s | 67.1 s | 1 | 16/16 | complete |
| pair3-old | 13 | 89.7 s | 170.5 s | 1 | 16/16 | evaluation_failed |
| pair3-new | 7 | 95.3 s | 95.8 s | 2 | 16/16 | complete |

“Final draft ready” is the last completed draft-publication step, before old-method assessment/review finishes. The new physics sample deliberately needed two exchanges; its first draft was ready after 62.6 seconds. Old physics needed only one exchange. Do not interpret these independently authored tasks as identical workloads.

| Aggregate | Old | New |
|---|---:|---:|
| Retained trajectories / independent compile passes | 3 / 3 | 3 / 3 |
| Generation workflows reaching complete | 0 | 3 |
| Review/evaluation context failures | 3 | 0 (no local review) |
| Total API calls | 39 | 15 |
| Mean final-draft time | 96.1 s | 67.4 s |
| Mean time before old review / new termination | 102.1 s | 67.9 s |
| Mean end-to-end attempt time | 177.9 s | 67.9 s |
| Surrogate prompt tokens | 77,492 | 26,591 |
| Surrogate completion tokens | 13,227 | 5,246 |
| Surrogate total tokens | 90,719 | 31,837 |
| Ready / training approvals | 0 | 0 |

Surrogate token consumption fell 64.9%. These are counts from the declared offline BPE tokenizer, **not actual agent billing or internal reasoning tokens**. All outputs respected the 1024-surrogate-token completion cap. Every new request passed an 8192-token total-context preflight, including the full reserved output and 256-token reserve. No real Hugging Face inference model was used.

## Quality review

I checked each original brief/success criteria against its actual files, tool observations, private notes and delivered replies, then recomputed numerical claims independently. [review.json](review.json) gives per-case findings under a rubric frozen before execution. This was a single **unblinded** review; its scores are descriptive, not validated quality measurements.

- **Memory retrieval:** both methods chose the final arrangement rather than superseded notes. New mode also combined two parallel reads and correctly calculated a £25 remaining balance. Both replies were concise.
- **Statistics:** the old sample correctly distinguished representative sampling from a larger self-selected poll and calculated the infeasible $1,600 random-sample target. Its answer would benefit from shorter, scannable formatting. The new sample correctly solved an integer allocation tradeoff: A800/B400, 162 expected signups, 12 expected complaints, with an explicit warning that expectations are not guarantees.
- **Physics:** the old sample correctly separated lower ramp force from greater total work. The new sample asked for missing entry speed instead of leaking the author-only value, then correctly chose the larger brake using 167.72 J and 1.06 m versus 1.31 m stopping distances. Its second file read was justified because earlier source/private-note windows had closed.

All six compile without diagnostics, close their windows, mask external observations, and supervise visible tool requests. Across 609 sampled supervised visibility queries, no future token or same-tick generated key was visible. BOS/EOS/NOOP were present. Per-case deterministic audits and independently computed arithmetic are included. These checks plus manual inspection support these six examples; they do not prove general model quality or diversity.

## Why the old workflows stalled

All old workers asked for additional context during the first bounded **layout review** and exhausted the available context-request/retry budget. They often demanded later whole-trajectory evidence while evaluating group-scoped rules and requested whole-window history where exact units were needed. This is an interaction between conservative worker behavior and the bounded-review access protocol, **not evidence that their final task answers were wrong**, nor proof the original method always fails.

The old runs spent 14 calls on layout review/context retries. Their pre-review generation path used 25 calls versus 15 new-mode calls (40% fewer). One old statistics reply needed an additional syntax retry after invalid JSON. Each old run also made a novelty call against its own initial planning draft's empty concept summary; no other experiment's trajectory was exposed. Excluding those three novelty calls and that syntax retry still leaves 35 total old calls versus 15 new calls.

The proxy implements chat-completion transport and SSE but does **not** grammar-constrain `response_format`. It relays raw worker text, so the observed JSON-format retry is not an error-rate estimate for OpenAI's constrained-output implementation. Original invalid text and all retries remain in the API logs. No results were repaired or rerun after seeing the comparison.

## Experimental controls and limits

- Three paired seeds select the same domain/difficulty/user traits for old/new: memory retrieval / simple / impatient novice; statistics / competing priorities / distracted novice; physics / moderate / terse practical experience. Ideas and files were authored independently, so difficulty within a pair differs.
- Six separate empty SQLite projects prevent cross-sample novelty or history leakage. All six workers start behind a readiness barrier, use the same inherited agent model, and have separate opaque mailboxes. No worker sees method/pair labels or other outputs.
- Isolation is by instructions and mailbox routing, **not an OS security boundary**. Each worker retains its own conversation history and was told to treat each current API request as stateless. This cannot fully reproduce fresh model context per request.
- Runs overlap. Measured time includes worker reasoning, tool/file interaction, polling and shared service scheduling. The proxy emits a completed answer in one SSE content chunk, so these are turnaround/workflow timings, **not GPU decode speed or streaming throughput**.
- Only three cases per method, one unblinded reviewer, no statistical significance claim, no small-local-model quality claim. Memory examples both used pottery notes and physics examples both used carts/ramps despite isolation; this illustrates shared-model archetype repetition, not a diversity guarantee.
- New-mode “complete” means generation completed; it does not mean semantic validation or approval. Old-mode evaluation failures leave usable drafts. All six remain state 0; this bundle is not an approved training export.

## Files and reproduction

- [all-trajectories.jsonl](all-trajectories.jsonl): all six original importable trajectory sources.
- `cases/<case>/`: original trajectory JSON, readable transcript, canonical compiled summary/tool results, deterministic audit, actual API request/response log, generation job/checkpoint, and a SQLite project for immediate replay.
- [metrics.json](metrics.json), [review.json](review.json), [plan.json](plan.json): exact counters, review evidence, paired conditions and source revision.
- [agent_api_proxy.py](agent_api_proxy.py), [mailbox_worker.py](mailbox_worker.py), [run_experiment.py](run_experiment.py): proxy, worker protocol and exact experiment harness. `test_proxy.py` checks streaming/non-streaming transport and mailbox routing; both tests passed.
- `inference-tokenizer/` and `training-tokenizer.json`: declared surrogate context counter and exact embedded Kidra training-tokenizer fixture.

Run scripts with the repository's `.[inference,test,mcp]` dependencies. The exact harness uses the recorded `/workspace/trajectory-remix` path; adjust its ROOT/WORK constants for another checkout, and use a fresh work directory. Run `prepare`, start the loopback proxy on port 8791, start the harness, and launch six fresh workers using the mailbox protocol. The saved readiness barrier excludes worker startup from run times.

To inspect saved outputs, start Trajectory Remix with `--dataset cases/<case>/dataset.sqlite`. To combine the JSONL sources, initialize a new project with the supplied `training-tokenizer.json`, import `all-trajectories.jsonl`, and retain original metadata/checklists. No approval is granted by importing or compiling.
