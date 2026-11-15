# KTREMIX training demo

The demo is a small end-to-end check that real compiled data can actually train a model. It
generates tiny letter-addition trajectories, compiles them with the real
[graph compiler](GRAPH-COMPILER.md), writes compressed [KTREMIX](KTREMIX.md) shards, samples
them, and trains a two-layer, ~50k-parameter model with a global shared key/value memory.

It's for validating the pipeline, not for producing a useful model. One complete CPU run has
passed its gates, and the numbers are in [Validation results](VALIDATION-RESULTS.md). They are one
run's observations, not guarantees.

## Run it

Training is explicit and refuses to run in CI. It needs no endpoints or pretrained weights.

```bash
python -m pip install -e '.[export]'
python -m pip install 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu
python examples/train_ktremix.py run /tmp/ktremix-graph-addition --user-machine --check
```

- The output directory must be new and under `/tmp`.
- For a GPU, install a matching PyTorch and add `--device cuda`.
- Defaults: 2,048 training and 256 held-out samples, 256 per shard, 3,000 steps, batch 16, seed 17,
  one CPU thread, evaluation every 500 steps on 64 free-running samples.
- `--check` enforces the convergence gates: **99%** held-out ordinary-letter accuracy and **95%**
  exact free-running timing and closure accuracy. It also requires external inputs that don't depend on
  agent outputs, so older result-gated datasets must be regenerated. Other devices and seeds need their
  own validation.

To run the stages separately:

```bash
python examples/train_ktremix.py generate /tmp/ktremix-graph-data --user-machine
python examples/ktremix_dataset.py verify '/tmp/ktremix-graph-data/*.ktremix'
python examples/train_ktremix.py train /tmp/ktremix-graph-data --user-machine --check
```

For ordinary correctness checks, run only the tiny fixtures, which need no PyTorch, training or live
model:

```bash
python -m pytest -q tests/test_graph_compiler.py tests/test_graph_export.py \
  tests/test_ktremix_format.py tests/test_graph_demo.py
```

`tests/test_ktremix_training.py` adds model parity checks and exactly **one** optimizer update.

## The task

Two read-only document windows receive ten letters and ten digits. The agent must produce, for each
position, the letter shifted by that digit (mod 26): `b + 2 = d`, `z + 1 = a`.

- The vocabulary has 88 entries: 36 ordinary symbols (`a`–`z`, `0`–`9`), twelve logical controls and
  40 framing symbols. The tokenizer never silently drops required CTX headers, and its exact
  serialization is pinned in each dataset.
- External windows and their headers are masked. There are three peak Active windows plus one
  Available reserve, with seeded random physical slots.
- Framing uses compact wordpieces, so a header takes 15 streamed ticks instead of 58 character ticks,
  without changing the compiler or header protocol.

### Timing

The result window's CTX header is generated one token per tick, concurrently with the first external
inputs at tick zero. Each input depends only on the previous input in its own stream, and optionally on
its same-index peer (`--max-gap` controls how often). No input depends on a header or a sum, so delaying or
deleting every agent event leaves the external schedule unchanged.

A sum can't land on a tick its header or the previous sum already uses, so results are scored at the
**earliest legal tick**: `max(input_ready_tick, header_or_previous_sum_finish_tick + 1)`. The project owner chose this policy on November 14, 2026, after the original
rule turned out to conflict with streaming headers.
Reports show the scored policy and expose the older "input-ready" check as a separate diagnostic. A dataset
that gates results on the sums can't pass `--check`.

Because all inputs usually arrive before the header finishes, the demo doesn't test learned waiting over
long delays.

### Loss balance

Fixed framing would otherwise swamp the ten answers, so the loss is weighted per whole event: result
header 0.1, implicit NOOP 0.1, each sum event (including its closing EOS) 5, external events masked. Dataset
identities are versioned `graph-addition/3`. Reported `letters_accuracy` counts only the ten sums after
CTX_END, and `header_letters_accuracy` is separate so header prediction can't inflate the gate.

## The model

A dense, inspectable reference, not an optimized runtime: 50,208 parameters, two layers of width 48, four
query heads, one shared K/V head of width 12, feed-forward width 144, tied output embeddings, four
physical-slot embeddings and 128 position embeddings, plus fixed per-head relative-position biases
(clipped at 64). The bias is a general positional prior, with no arithmetic rule or target-dependent mask.

- Every layer reads a shared global K/V bank across visible windows. Queries start from the previous visible
  token in their own window, never the label.
- For each query, keys that have since been **closed or replaced** are excluded in every layer, and memory
  is rebuilt without them. Erased text therefore can't survive in another window's hidden state. Queries
  sharing a state share a memory bank, and tiny tests compare logits and gradients with a per-query reference.
- Don't compare its speed with older adapters, or detach prefixes to make it faster.

Training samples shards uniformly with CRC checks. Evaluation feeds the model's own outputs back in for headers
and results, using the source only to score afterwards. Early or late outputs, malformed tokens, missing or wrong
EOS, and reserve mistakes all fail exact scoring, with a bounded horizon to catch missing closure.

## What it produces

`tokenizer.json`, raw `train.jsonl` and `validation.jsonl`, split-specific shards, and `build.json` come from
generation. `training.json`, `last-command.json` and `model.pt` come from training. The demo's held-out split is
called `validation`, while general project export uses Train, Development and Test and keeps families together.

## What remains unproven

Live-model quality hasn't been validated, because external model connections were blocked in the test
environment. The scale and stress acceptance was waived by the project owner. The harness stays available for
later use. See [Acceptance](ACCEPTANCE.md).
