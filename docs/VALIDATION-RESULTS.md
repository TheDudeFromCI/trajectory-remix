# Integration validation results

These observations cover the graph compiler/training demo and provider contract
checks. They do not establish production throughput or generated-content quality.
No stress workload was run.

## Real compiler → compressed shards → sampler → training

The 50,208-parameter, two-layer shared-KV global-attention model passed the
unchanged `--check` gates on CPU at seed 17 after 3,000 updates. The dataset had
2,048 training and 256 held-out sources, compiled by the real graph compiler.
It retains 36 ordinary letters/digits, automatic headers, three Active windows
plus Available, independent external arrivals, and earliest-legal output timing.

- Held-out arithmetic letters: **99.8828125%** (headers excluded).
- Held-out NOOP and EOS: **100%** each.
- Held-out exact teacher-forced trajectories: **98.828125%**.
- Teacher-free timing and closure: **64/64 trajectories**, all 640 sums correct.
- Malformed teacher-free trajectories: **0**.

[Machine-readable report](validation/graph-training-cpu.json) includes settings,
versions, learning history, ordinary training phase timings and example rollouts.
These are one run's observations, not guarantees. Reproduce with:

```bash
python examples/train_ktremix.py run /tmp/remix-graph-training --user-machine --check
```

Validation exposed two demo problems: header letters had inflated the reported
arithmetic metric, and fixed framing dominated its training loss. Task accuracy
now counts only body letters after CTX_END, and whole-event weights balance the
task against headers/NOOP. Compact framing wordpieces and a generic fixed
relative-position prior make retrieval practical without an arithmetic oracle,
target-dependent attention mask, or compiler/header-protocol change. Small
regressions compare grouped and per-query logits/gradients and enforce causal,
closed-window and replacement invariance.

The short fixture still does not prove general long-delay waiting or broad LLM
capabilities. See the [demo contract](KTREMIX-TRAINING-DEMO.md).

## Live provider limitation

Hugging Face downloads and a single minimal OpenAI connectivity attempt were
blocked by the execution environment's proxy with HTTP tunnel 403 Forbidden.
The OpenAI origin returned no response or usage. No repeated API attempts were
made, and the temporary credential was deleted rather than stored in the project,
repository or report. Real provider/content quality therefore remains unvalidated.

Provider HTTP regressions cover strict schema requests and JSON-object mode for
contracts outside the common strict subset; the complete local schema remains
authoritative. JSON mode changes only serialization, never endpoint/model routing.
No fake-provider verdict is presented as live-model evidence.
