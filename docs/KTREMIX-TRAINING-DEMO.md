# KTREMIX graph-compiler training demo

The letter-addition harness generates `kidra-trajectory-graph/1` sources, uses the
real [graph compiler](GRAPH-COMPILER.md), writes compressed KTREMIX shards and
samples them for a two-layer global shared-KV model. The old replay compiler and
fixture-specific lowering adapter have been removed from this path.

## User-machine commands

Full training and throughput measurements are user-machine acceptance, never CI
or assistant validation. No provider endpoints or pretrained weights are needed.
The updated model's convergence has not been measured.

```bash
python -m pip install -e '.[export]'
python -m pip install 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu
python examples/train_ktremix.py run /tmp/ktremix-graph-addition --check
```

Use an appropriate PyTorch install and `--device cuda` for GPU work. The generated
directory must be new and under `/tmp`. Defaults remain 2,048 training samples,
256 held-out samples, 256 samples/shard, 3,000 steps, batch 16, seed 17, one CPU
thread, evaluation every 500 steps and 64 free-running evaluation samples.
`--check` requires held-out ordinary-letter accuracy of 99% and exact free-running
timing/closure accuracy of 95%; these are unverified convergence gates for the
new framed task, not results already obtained.

Separate generation, conformance and training:

```bash
python examples/train_ktremix.py generate /tmp/ktremix-graph-data
python examples/ktremix_dataset.py verify '/tmp/ktremix-graph-data/*.ktremix'
python examples/train_ktremix.py train /tmp/ktremix-graph-data --check
```

For ordinary local correctness, run only the tiny fixture suite:

```bash
python -m pytest -q tests/test_graph_compiler.py tests/test_graph_export.py \
  tests/test_ktremix_format.py tests/test_ktremix_training.py
```

It generates a few records and performs exactly one optimizer update. It also
checks free-running runtime parity with prescribed mock predictions, without a
training campaign, live LLM call or throughput measurement.

## Task, framing and timing

The task has 36 ordinary symbols (`a`–`z`, `0`–`9`), twelve logical controls and
34 dedicated framing symbols (punctuation/whitespace), for 82 vocabulary entries.
The tokenizer never silently drops required CTX headers. Its exact serialization
and special map are pinned in each dataset.

Two read-only document windows receive ten letters and ten digits. Result i is
`(letter_index[i] + digit[i]) % 26`: `b + 2 = d`, `z + 1 = a`. External windows,
including their headers, are masked. There are three peak Active windows plus
one Available reserve, with seeded randomized physical slots.

A root empty-content agent event generates the result window's required CTX
header one token per tick. This activates Available and creates its replacement.
Only after that startup do the first inputs arrive. External headers are atomic.
Each pair depends on the previous result; one stream may additionally depend on
the other stream with `chain_immediately=false`, producing variable arrivals
through actual dependencies. The retained `--max-gap` CLI option now controls
how often this extra dependency is chosen; it does not create tick delays.

Every sum starts on the earliest tick both inputs and the prior result are
complete. Its single letter is generated immediately; the last event's EOS
follows one tick later. The explicit fixture accounts for header startup, input
framing, NOOP decisions and the agent-completion-to-next-tick rule. Generic wait
events, replay tick anchors and disabled-header compatibility paths are absent.

Writable windows and Available predict supervised NOOP when idle; read-only
windows count as NOOP without predictions. All-NOOP sleeps retain one decision
and resume only on external input. No EOS token is ever a KV key.

## Model and causal context

The model has **49,920 parameters**, two layers, width 48, four query heads, one
shared K/V head of width 12, feed-forward width 144, tied output embeddings, four
physical-slot embeddings and 128 position embeddings. Framing expanded the
vocabulary/positions; reducing feed-forward width preserves the approximately
50k-parameter target without bypassing protocol rules.

Every layer uses a shared global K/V bank across visible windows. Queries start
from the previous visible own-window token, never the label being predicted.
Memory construction obeys original causal frontiers; queries cannot see their
own targets, future inputs or simultaneous outputs. For each query, every layer
also excludes keys that have since been closed or replaced. Rebuilding that
query's memory prevents erased content from surviving indirectly in another
window's hidden state. Padded samples never attend across trajectories.

This is a dense, inspectable reference model, not an optimized KV cache/runtime.
It recomputes memory for each query and backpropagates through complete samples.
Do not compare its throughput with the old adapter's historical numbers or
silently detach prefixes to make it faster.

Training uniformly samples compressed shards, with CRC checks. Teacher-forced
metrics include required header tokens, ordinary task letters, NOOP and EOS.
Free-running evaluation feeds back model emissions for headers and result text;
source agent content is used only for scoring afterwards. External fixture
stimuli follow their nominal compiled schedule. Reserve activation, early/late
outputs, malformed tokens and missing/wrong EOS fail exact scoring. A bounded
horizon catches missing closure.

## Outputs and outstanding acceptance

`tokenizer.json`, raw `train.jsonl`/`validation.jsonl`, split-specific compressed
shards and `build.json` identify the graph dataset and compilation counts.
`training.json`, `last-command.json` and `model.pt` are produced by user-run
training. The demo's held-out split is named `validation`; general project export
uses Train/Development/Test and preserves family grouping before sharding.

[The historical CPU report](benchmarks/ktremix-training-cpu.json) measured the
removed replay/fixture-adapter path, vocabulary 39 and model size 49,968. Those
measurements predate the graph integration and do not validate this compiler,
new header timing, model convergence or recomputed memory behavior. They were
not rerun for this implementation.

Outstanding user-machine checks: full `run --check`, local-device memory and
throughput measurements, broader local-LLM integration after generation workers
land, and 25k stored/20 active acceptance after studio/jobs integration. Small
array, visibility, mock-runtime and one-update correctness checks are separate
from those gates.
