# KTREMIX end-to-end training demo

This is a small, runnable training experiment and format integration check. It
generates raw trajectory sources, compiles them, writes compressed `.ktremix`
shards, samples those files during every training step, and trains a model from
random initialization. No API endpoints or pretrained weights are needed.

## Run

From a repository checkout, in a Python 3.11+ virtual environment:

```bash
python -m pip install -e .
python -m pip install numpy lz4
# CPU installation; use your appropriate PyTorch install for a GPU instead.
python -m pip install 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu
python examples/train_ktremix.py run /tmp/ktremix-addition --check
```

The output directory must not already exist when generating. Defaults are 2,048
training trajectories, 256 held-out trajectories, 256 trajectories per shard,
3,000 steps, batch size 16, seed 17, one CPU thread, and evaluation every 500
steps. The default free-running evaluation covers 64 held-out trajectories.
`--check` exits unsuccessfully unless held-out letter accuracy reaches 99% and
exact free-running trajectory accuracy reaches 95%. These are demo smoke gates,
not a guarantee that every seed/device/version converges identically.

For a quick plumbing run, without a convergence requirement:

```bash
python examples/train_ktremix.py run /tmp/ktremix-smoke \
  --train-samples 16 --validation-samples 4 --shard-size 7 \
  --steps 2 --eval-every 1 --rollout-count 2
```

Data generation/compilation and training can also be run separately:

```bash
python examples/train_ktremix.py generate /tmp/ktremix-addition-data
python examples/ktremix_dataset.py verify '/tmp/ktremix-addition-data/*.ktremix'
python examples/train_ktremix.py train /tmp/ktremix-addition-data --check
```

Use `--device cuda` for GPU training. Timing synchronizes CUDA at phase
boundaries; the demo favors inspectable measurements over maximum GPU overlap.
`--max-gap` controls random external arrival spacing. `--rollout-count 256`
scores the entire default held-out set without teacher forcing.

## Task and timing

There are 36 non-special tokens: lowercase `a`–`z` and digits `0`–`9`.
The only special tokens are BOS, EOS and NOOP, for a vocabulary of 39.

- Window 1 receives ten uniformly sampled letters from an external source.
- Window 2 receives ten uniformly sampled digits from an external source.
- Both inputs, including their BOS context, are masked from loss and read-only.
- The result begins as the Available window. Its first model-generated letter
  activates window 3, immediately creating a new implicit BOS-only reserve.
- Result i is `(letter_index[i] + digit[i]) % 26`, converted to a letter:
  `b + 2 = d`; `z + 1 = a`.
- The model must emit each result on the **first tick both corresponding values
  are available**. Each input stream advances by independently sampled gaps of
  one through `--max-gap` ticks, so successive results cannot collide.
- The model emits EOS one tick after its tenth result. Input windows may stay
  open. There are three Active windows plus the implicit Available window.

External text is committed before the model prediction in a shared tick. When
waiting, writable windows emit supervised NOOPs; read-only windows do not require
model predictions. The reserve's NOOP is also trained. The all-NOOP decision
frame is retained, then sleep skips idle ticks until an external update wakes
execution. No repeated idle model passes are needed for a long wall-clock gap.

The raw files use the repository's existing replay JSON schema, with headers
disabled to preserve the requested small vocabulary. The demo runs the actual
`Compiler.compile`, then a **fixture-only** export adapter applies progressive
Available assignment, read-only behavior, sleep collapse, and frontier encoding.
This is not the planned general graph compiler or production Export integration.
Its manifest uses the distinct contract `ktremix.letter-addition-progressive/1`.

## Model and attention

[`examples/train_ktremix.py`](../examples/train_ktremix.py) defines a transformer
with **49,968 trainable parameters**, two layers, width 48, four query heads,
one shared K/V head of width 12, and feed-forward width 184. Output weights are
tied to the token embedding. Learned window and insertion-position embeddings
cover four physical slots and twelve positions (BOS, ten values, EOS query).

Every layer uses one global K/V bank spanning all visible windows. The same
projections serve memory construction and prediction queries. Both updates read
the bank from before that layer's update. This is a deliberately small global
shared-KV demonstration, not a full Kidra architecture or optimized KV runtime.

The loader forms two kinds of state:

1. Durable memory rows start from their own tokens. Each layer masks their
   attention according to their original **construction** frontier.
2. Prediction queries start from the previous visible token in their own window,
   with the current insertion position. Their target token is a label only.

Both paths use the file's visibility intervals. A target cannot see its own
label, later arrivals, or simultaneous model outputs, directly or through hidden
states constructed using future text. NOOP decisions never enter memory. Padded
batch elements cannot attend across samples. The model receives no raw source,
metadata, expected sums, arrival schedule, or author-only fields.

This tiny experiment builds dense attention masks. Full trajectories participate
in backpropagation; there is no detached prefix activation cache. For production
trajectories use an appropriate sparse attention/training adapter; the binary
format does not require dense masks.

## Evaluation and outputs

Training samples are read from compressed files with the public `Dataset`
sampler, uniformly across global trajectory indices, with CRC checks enabled.
It does not train from the raw JSON or cache a decoded training dataset. The
model trains on all writable-window decisions, including NOOP and EOS.

Held-out evaluation reports letter, NOOP and EOS accuracy, plus the fraction of
trajectories with **every** supervised decision correct. Teacher forcing is used
only for this conventional loss/accuracy measurement.

Free-running evaluation starts from BOS and external stimuli and feeds back
the model's actual emissions. Authored results are used only for scoring. Early
letters, late letters, missing/incorrect EOS, extra reserve-window activity and
malformed emissions are failures. The small runtime recomputes representations
from historical rows with frontier masks; it is not a throughput-optimized
inference engine. A bounded horizon catches missing closure.

The output directory contains:

| File | Purpose |
|---|---|
| `tokenizer.json` | Exact tokenizer whose SHA-256 is embedded in every shard |
| `train.jsonl`, `validation.jsonl` | Raw generated Remix sources, with disjoint IDs |
| `train-*.ktremix`, `validation-*.ktremix` | Compressed compiled training shards |
| `build.json` | Generation, compilation, write/compression time, counts and size |
| `training.json` | Model/configuration, loss history, held-out metrics, rollout and throughput |
| `last-command.json` | Command options and total wall time, including all phases for `run` |
| `model.pt` | Locally produced PyTorch state dictionary |

Use a fresh directory to retain multiple runs. The training command replaces
its own report/checkpoint files; it does not mutate the immutable dataset shards.

## Measured reference run

One Linux x86-64 CPU run, Python 3.12.14, PyTorch 2.15.0+cpu, one thread, seed 17,
and the defaults above except `--rollout-count 256` produced the following.
The complete command passed `--check`; its machine-readable measurements are in
[`benchmarks/ktremix-training-cpu.json`](benchmarks/ktremix-training-cpu.json).

| Measure | Result |
|---|---:|
| Raw sources generated | 2,304 trajectories / 7,607,327 bytes |
| Compiled shards | 4,673,984 bytes |
| Source generation | 0.105 s |
| Replay compilation + fixture lowering | 1.911 s |
| Validation/compression/shard writing | 0.736 s |
| Training steps | 3,000 × 16 trajectories |
| Sampling/CRC/decompression over training | 0.796 s |
| Collation and CPU tensor preparation | 1.658 s |
| Forward/backward/optimizer | 25.390 s |
| Training wall time including periodic validation | 28.343 s |
| Full command, generation through checkpoint/report | 34.049 s |
| Training throughput, including load/collation/optimizer | 1,724 trajectories/s |
| Training throughput in supervised decisions | 78,757 targets/s |
| Initial → final held-out loss | 3.639 → 0.00510 |
| Held-out letter accuracy | 99.92% |
| Held-out NOOP accuracy | 99.99% |
| Held-out EOS accuracy | 100% |
| Exact held-out trajectories, teacher forced | 254 / 256 |
| Exact free-running timing and closure | 254 / 256 |

Free-running letters-at-correct-tick accuracy was **99.92%**, including one
premature-letter error. An initial run's 64-example subset was perfect; the full
held-out evaluation above is more representative.

These are actual observations from one run, not convergence or performance
guarantees. Files were freshly written to local storage, so the OS page cache was
warm. Training throughput counts repeated sampled presentations, not unique
trajectories; it excludes periodic evaluation, generation and checkpoint saving.
The separately reported full command wall time includes those phases. CPU and
GPU results are not interchangeable. This small task checks that storage,
causality and training connect in practice; it does not validate every general
compiler behavior, model capability, or production-scale performance target.

## Regression checks

```bash
python tests/test_ktremix_format.py
python tests/test_ktremix_training.py
```

Training tests cover vocabulary/wraparound, earliest output ticks, masked inputs,
one Available window, sleep collapse, parity with the current compiler's visible
memory on shared rows, future-token invariance through both layers, compressed
round trips, and a real optimizer update. They skip when optional demo
dependencies are absent. The longer convergence check is the explicit `run
--check` command, keeping routine test runs fast.
