# KTREMIX graph-compiler training demo

The letter-addition harness generates `kidra-trajectory-graph/1` sources, uses the
real [graph compiler](GRAPH-COMPILER.md), writes compressed KTREMIX shards and
samples them for a two-layer global shared-KV model. The old replay compiler and
fixture-specific lowering adapter have been removed from this path.

## User-machine commands

Full training is explicitly invoked and refused in CI. No provider endpoints or
pretrained weights are needed. Following user authorization, one complete CPU
run passed both convergence gates; see [observed results](VALIDATION-RESULTS.md).
No stress workload was run, and these observations are not performance guarantees.

```bash
python -m pip install -e '.[export]'
python -m pip install 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu
python examples/train_ktremix.py run /tmp/ktremix-graph-addition --user-machine --check
```

Use an appropriate PyTorch install and `--device cuda` for GPU work. The generated
directory must be new and under `/tmp`. Defaults remain 2,048 training samples,
256 held-out samples, 256 samples/shard, 3,000 steps, batch 16, seed 17, one CPU
thread, evaluation every 500 steps and 64 free-running evaluation samples.
`--check` requires held-out ordinary-letter accuracy of 99% and exact free-running
timing/closure accuracy of 95% under the approved earliest-legal policy below.
It also requires external inputs independent of agent outputs; old result-gated
datasets must be regenerated. These are the enforced convergence gates; the
recorded seed-17 CPU run achieved 99.8828125% held-out sum accuracy and 64/64
exact teacher-free rollouts. Other devices/seeds require their own validation.

Separate generation, conformance and training:

```bash
python examples/train_ktremix.py generate /tmp/ktremix-graph-data --user-machine
python examples/ktremix_dataset.py verify '/tmp/ktremix-graph-data/*.ktremix'
python examples/train_ktremix.py train /tmp/ktremix-graph-data --user-machine --check
```

For ordinary local correctness, run only the tiny fixture suite:

```bash
python -m pytest -q tests/test_graph_compiler.py tests/test_graph_export.py \
  tests/test_ktremix_format.py tests/test_graph_demo.py
```

These checks need no PyTorch, optimizer update, training campaign, live LLM call
or throughput measurement. The optional `tests/test_ktremix_training.py` suite
adds model/runtime parity checks and exactly one optimizer update.

## Task, framing and timing

The task has 36 ordinary symbols (`a`–`z`, `0`–`9`), twelve logical controls and
40 dedicated framing symbols (punctuation/whitespace), for 88 vocabulary entries.
The tokenizer never silently drops required CTX headers. Its exact serialization
and special map are pinned in each dataset.

Two read-only document windows receive ten letters and ten digits. Result i is
`(letter_index[i] + digit[i]) % 26`: `b + 2 = d`, `z + 1 = a`. External windows,
including their headers, are masked. There are three peak Active windows plus
one Available reserve, with seeded randomized physical slots.

The demo tokenizer encodes fixed JSON field names, purpose values and `null` as
framing wordpieces; ordinary task symbols remain one token each. This keeps the
required header at 15 streamed ticks instead of 58 character ticks without
changing the compiler/header protocol or hiding headers.

A root empty-content agent event generates the result window's required CTX
header one token per tick, concurrently with the first external inputs at tick
zero. This activates Available and creates its replacement. External headers are
atomic. Each input depends only on its stream's preceding input, with an optional
dependency on its same-index peer. `chain_immediately=false` staggers arrivals
through external dependencies. No input depends on a header or sum: deleting or
delaying every agent event leaves the external schedule unchanged. The retained
`--max-gap` option controls how often the peer dependency is chosen; it does not
create tick delays.

The original task had a conflict: emit each sum on the first
tick both inputs are ready, while streaming the agent-created result header.
With the former character-only tokenizer, the header occupied ticks 0–57; the first pair is ready at
tick 0 or 1. A sum cannot occupy a tick already used by its header. Later sums
also require a separate tick after the previous agent output. After this conflict
was explained, the user explicitly selected streamed headers, independent
arrivals and earliest-legal result timing on November 14, 2026. The compiler and
header protocol stay unchanged.

The executable fixture emits at the earliest **legal** tick:
`max(input_ready_tick, header_or_previous_sum_finish_tick + 1)`. Its single
letter is generated then; the last event's EOS follows one tick later. A pure
`timing_report` exposes input-ready, earliest-legal and compiled letter ticks.
Training rollout reports identify this scored policy and separately expose
`input_ready_timing_satisfied` as a diagnostic of the prior literal criterion.
That diagnostic is not the newly agreed timing gate. A result-gated dataset
cannot pass `--check` even if nominal schedule accuracy is perfect.

For these short streams, inputs may all arrive before the header completes; the
fixture therefore does not establish learned waiting across later arrivals.
Generic wait events, replay tick anchors and disabled-header paths remain absent.

The fixture balances loss so the ten arithmetic outputs are not drowned out by
fixed framing: result-header weight is 0.1, implicit NOOP weight is 0.1, and each
sum event (including its closing EOS) has weight 5. External events remain masked.
These are whole-event weights, not token-level exceptions. Dataset identities are
versioned as `graph-addition/3` so regenerated shards identify the changed objective.
Held-out `letters_accuracy` counts only the ten sums after CTX_END;
`header_letters_accuracy` is reported separately. Header prediction cannot inflate
the task accuracy gate.

Writable windows and Available predict supervised NOOP when idle; read-only
windows count as NOOP without predictions. All-NOOP sleeps retain one decision
and resume only on external input. No EOS token is ever a KV key.

## Model and causal context

The model has **50,208 parameters**, two layers, width 48, four query heads, one
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
It rebuilds memory for each distinct expiry state and backpropagates through complete samples. Queries sharing that state reuse a memory bank; the original construction frontier still excludes future keys at every layer. Closed/replaced keys are removed before rebuilding, preventing indirect resurrection. Tiny tests compare both logits and parameter gradients against the per-query reference.
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

Historical replay/fixture-adapter measurements predate this graph integration
and have been removed from the public result path. They do not validate new
header timing, convergence or recomputed-memory behavior.

The complete `run --user-machine --check` has now passed on CPU as recorded in
[validation results](VALIDATION-RESULTS.md). Live-model quality remains unvalidated
because external model/API connections were blocked by the execution proxy.
The user waived scale/stress acceptance; the manual harness remains opt-in for
future use. CLI training commands still require explicit invocation and refuse CI.
Small array, visibility, mock-runtime and optimizer fixtures remain separate.

The tiny model uses fixed per-head relative-position biases (offsets clipped to
64 positions) in both memory construction and prediction attention. This supplies
a general positional inductive bias for retrieving aligned data across windows;
it contains no arithmetic rule, target-dependent mask, or forced input lookup.
Causal and lifetime masks are applied after the bias and remain authoritative.
The grouped implementation is checked against per-query logits and gradients,
including closed/replaced-memory cases.
