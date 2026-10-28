# Content-first user-scenario comparison

Recorded October 28, 2026:

```sh
python scripts/benchmark_content_first.py --samples 3 --seed 42 --output-dir work/noval-final-comparison
```

This **offline integration benchmark uses scripted content and simulated inference delays**. It compares the original `user_problem` generator with `user_scenario_noval`, using the same inherited domain/difficulty pools, roles/traits, tag seed, authored arithmetic problem and actual calculator tool results. Both produce `#user_scenario` samples. The fast variation keeps the original external checklist but does no local semantic review. Optimized content prompts intentionally differ.

The fixture trains an offline byte-level BPE inference tokenizer from bundled scenario text and tool contracts. Its training tokenizer adds Kidra lifecycle tokens. These are fixture token counts, not measurements of a deployed model. Both endpoints use an 8192-token configured context and 1024-token total completion cap. Every fast request passes its own context preflight. No download or inference hardware is required.

| Metric | `user_problem` | `user_scenario_noval` |
| --- | ---: | ---: |
| Candidates attempted / retained | 3 / 3 | 3 / 3 |
| Successfully constructed drafts | 3 | 3 |
| Model requests | 45 | 12 |
| Prompt tokens, fixture tokenizer | 61,151 | 16,833 |
| Completion tokens, fixture tokenizer | 5,577 | 441 |
| Total tokens | 66,728 | 17,274 |
| Sum of simulated request durations | 4.195 s | 0.923 s |
| Measured fixture/orchestration elapsed | 9.708 s | 4.310 s |
| Constructed candidates / elapsed second | 0.309 | 0.696 |
| Candidate retention | 100% | 100% |
| Externally repaired / Ready approved | 0 / 0 | 0 / 0 |
| Usable training samples / second | 0 | 0 |

The fixture uses **73.3% fewer requests and 74.1% fewer tokens**, retaining all candidates in both modes. The fast path uses four calls per one-exchange sample: idea, user message, private planning/tool decisions, and grounded reasoning/response/continuation. Subsequent exchanges use three calls. It removes reference-solution, novelty-judging, separate reasoning/answer, assessment, validation and repair requests. The original uses its existing review grouping and novelty configuration, so its request count can vary with stored candidates and review groups.

Simulated request time falls 78.0% and measured fixture elapsed falls 55.6%. These percentages reflect an artificial latency model (10ms per request, 100k prompt tokens/s and 5k generated tokens/s), local tokenization/compilation, and host scheduling. They do not establish GPU savings. No reasoning counters exist in this fixture. The [recorded JSON report](content-first-benchmark.json) retains exact counters and limitations; the script preserves SQLite projects and inspectable samples in a fresh output directory.

The earlier standalone parallel-construction comparison remains available with `--scenario-family content-demo`. It is a construction demonstration; `user_scenario_noval` is the production variation sharing the original generation inputs.

Use `--live --base-url ... --model ... --inference-tokenizer ... --training-tokenizer ...` for real model measurements. No live endpoint/model artifacts were supplied here. Semantic quality, diversity, repair effort, hardware savings and approved-sample throughput remain unmeasured. The benchmark never approves samples and reports reductions only when both modes construct the requested candidate count.
