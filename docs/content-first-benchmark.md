# Content-first comparison

Recorded October 28, 2026. Command:

```sh
python scripts/benchmark_content_first.py --samples 3 --seed 42 --output-dir work/integrated-comparison
```

This is an **offline integration benchmark with scripted outputs and simulated inference delays**, not a measurement of local LLM hardware or semantic quality. Both runs use the bundled `content_first` scenario, identical content outputs and condition seed, three concurrent content requests, the same fixture inference tokenizer, an 8192-token content-first request limit, and a 1024-token total completion cap. Existing mode runs its local semantic validator; content-first retains the original checklist and defers review. The original YAML workflow already generates content; this comparison does not claim the baseline model authors full replay syntax.

| Metric | Existing YAML mode | Content-first |
| --- | ---: | ---: |
| Candidates attempted / retained | 3 / 3 | 3 / 3 |
| Successfully constructed drafts | 3 | 3 |
| LLM requests | 18 | 15 |
| Prompt tokens, fixture tokenizer | 55,737 | 14,812 |
| Completion tokens, fixture tokenizer | 1,836 | 1,743 |
| Total tokens | 57,573 | 16,555 |
| Sum of simulated request durations | 2.307 s | 1.605 s |
| Measured fixture/orchestration elapsed | 5.774 s | 3.749 s |
| Constructed candidates / elapsed second | 0.520 | 0.800 |
| Candidate retention | 100% | 100% |
| Externally repaired / Ready approved | 0 / 0 | 0 / 0 |
| Usable training samples / second | 0 | 0 |

The fixture removes 16.7% of requests and 71.2% of total tokens, primarily by eliminating whole-draft validator input. Summed simulated request durations decrease 30.4%; measured fixture elapsed decreases 35.1%. Those timing percentages depend on artificial delays, host scheduling and deterministic compilation overhead. They do not establish GPU inference savings. No provider reasoning-token counters exist in this fixture; zero recorded reasoning usage is not evidence about real reasoning models.

The fixture latency model is explicit in the script: 10ms per request, 100,000 simulated prompt tokens/second, and 5,000 simulated generated tokens/second. A fresh output directory is required, so prior measurements and candidate projects are preserved. `report.json` contains the counters, provenance and limitations; each mode's SQLite project and sample JSON support replay inspection. Reductions are reported only when both modes construct the requested number of candidates.

Structural fidelity is covered separately by regression tests: model-authored tool requests become supervised request tokens, actual calculator/file results become masked observations, parallel reasoning and requests overlap, delayed file results remain unavailable to earlier decisions, BOS/EOS/NOOP and visibility masks retain compiler semantics, and trajectories longer than 8192 tokens are assembled from individually bounded requests. Failure tests retain invalid JSON/schema responses, partial responses, and failed construction proposals; external MCP repair uses the original saved checklist and cannot grant export approval.

No live local inference endpoint or model artifacts were supplied in this environment. Real inference time, model-specific token savings, semantic quality, diversity, repair effort and approved-sample throughput remain unmeasured. Use the documented `--live` command with the server's actual tokenizer/template and total-completion parameter, externally review equivalent scenario outputs, and compare approved yield before drawing hardware or training-quality conclusions.

The [recorded JSON report](content-first-benchmark.json) preserves these exact counters and measurement limitations. The integrated revision passed all 948 Python tests and 120 optional frontend DOM checks.
