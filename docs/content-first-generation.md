# Content-first generation

Set `generation_mode: content-first` in a scenario's `flow.yaml`. The existing YAML mode remains the default (`existing`); legacy task generation, replay syntax, storage schema, and export formats are unchanged.

In this mode, the local model writes content and decisions. The application supplies replay events, tick schedules, protocol framing, and window lifecycle operations. `remix.validate` returns a deferred result without calling its endpoint or attempting local repairs. `remix.novelty` passes through the proposed idea and retains it as the concept without running a model judge. Scenario review prompts, semantic rules, original flow, randomized conditions, role profiles, and raw responses remain available for external review. Candidates stay at validation level **0** until reviewed; generation never approves training exports.

## Configure inference

Install the optional tokenizer support:

```sh
pip install -e '.[inference,mcp,test]'
```

Configure endpoints through the existing JSON API: `PUT /api/config` with body `{"config": <complete configuration>}` and the dashboard CSRF token (or use project configuration). For example, add these fields to the generator endpoint and these top-level settings:

```json
{
  "content_first": {"context_budget": 8192, "reserve_tokens": 256},
  "endpoints": [{
    "id": "generator",
    "base_url": "http://127.0.0.1:8080/v1",
    "model": "your-local-model",
    "inference_tokenizer": "/models/your-local-model",
    "context_size": 8192,
    "max_tokens": 1024,
    "completion_limit_parameter": "max_tokens",
    "temperature": 0.8,
    "timeout": 120,
    "api_key_env": "REMIX_GENERATOR_KEY"
  }],
  "tasks": []
}
```

This is a content-only configuration example. Preserve existing legacy tasks/endpoints if you also use the legacy generation loop. Legacy tasks still require their referenced endpoints even when disabled. The content-first runner needs no validator endpoint for deferred review. The settings API still checks endpoints required by other loaded scenarios; retain the original validator alias when loading the standard scenario alongside its fast variation.

`inference_tokenizer` is a **local Hugging Face tokenizer directory**, with the tokenizer artifacts and the exact `chat_template` used by the inference server. It is independent of the embedded Kidra training tokenizer. Files are loaded locally with remote code disabled; the generator does not download models. The endpoint and tokenizer template must agree about roles, special tokens, and generation prefixes. There is no heuristic fallback. Missing tokenizer configuration fails before inference, leaving a recoverable draft and diagnostics.

Each request must satisfy:

```text
actual chat-template prompt tokens
+ max_tokens (TOTAL visible output AND hidden reasoning)
+ reserve_tokens
<= min(content_first.context_budget, endpoint.context_size if known)
```

Schemas embedded in instructions, selected windows, role profiles, tags, and the assistant generation prefix are all counted. `max_input_tokens` optionally imposes an additional per-step input cap using the same inference tokenizer. Required inputs are never truncated. A rejected preflight preserves inputs and the original prompt for repair; it makes no inference request. For larger tasks, split requests and select relevant inputs/windows explicitly. A completed trajectory may exceed every individual request budget.

The direct OpenAI-compatible streaming boundary sends exactly the counted messages. Use `completion_limit_parameter: max_completion_tokens` for servers expecting that parameter. The configured server must enforce the chosen parameter as a **total completion cap including reasoning**. A server interpreting it only as visible text cannot provide this guarantee; use its total-completion parameter. Reported usage exceeding the cap or contradicting preflight is retained as a failed proposal. `stream_usage: false` supports servers without streaming usage counters; metrics then identify tokenizer estimates, and unreported hidden reasoning remains unknown. Full completion capacity is still reserved before inference.

## Author content and decisions

`llm.content` is the content-oriented spelling of `llm.generate`. Both share the bounded boundary inside a content-first flow. Requests can return plain text or objects validated against existing scenario schemas. Dependencies, `when`, `flow.repeat`, roles, randomized tags, retries, and fallback configuration remain available.

```yaml
generation_mode: content-first
steps:
  analyze:
    uses: llm.content
    prompt: analyze.md
    visible_windows: [chat, attachment]
    inputs:
      conditions: ${scenario.tags}
  critique:
    uses: llm.content
    prompt: critique.md
    visible_windows: [chat, attachment]
  parallel:
    uses: remix.assemble
    needs: [analyze, critique]
    inputs:
      streams:
        - window: analysis
          type: reasoning
          content: ${steps.analyze.output}
        - window: critique
          type: reasoning
          content: ${steps.critique.output}
```

Independent model steps run concurrently up to the existing `max_parallel` limit. They do not receive sibling outputs. `needs` schedules work; explicit `inputs` and `visible_windows` select data. Visible windows contain only committed compiler state at the request boundary. Explicitly selecting a missing or closed window fails rather than exposing unavailable evidence; wildcard selection includes only open windows. Do not supply answer keys, unseen external observations, or author-only solutions to a request writing Kidra decisions. Original scenario requirements belong in world generation and external review; Kidra reasoning must use evidence available in replay.

`remix.assemble` schedules each stream at the current committed frontier plus an optional nonnegative `offset`. No absolute/backdated start is accepted. Streams in a batch use distinct windows and overlap naturally, emitting one training token per tick. A later assembly step can continue an open window. Optional fields are `type`, `op` (`generate` or external `append`), `source`, `offset`, `close`, and `loss`. Generated streams retain the compiler's normal loss policy; external appends are masked by default. `close: true` emits EOS for generated streams. External windows close through a later generated lifecycle operation. Special trajectory tokens or anchors inside prose fail construction and are retained for repair.

Omitting a stream window allocates a unique reasoning window automatically. A batch may take `call_groups` (sequential groups of parallel calls), or `calls`, a flat list of **model-generated** `{tool, arguments}` decisions. Their request windows start alongside its reasoning streams. The compiler serializes and supervises the requests; actual tool results remain external observations with their normal masks. `release_windows: [id, ...]` optionally closes model-selected obsolete windows with generated EOS after all batch consumers finish. Missing/closed IDs, duplicates and persistent system/tools/notes windows are rejected transactionally; raw construction proposals remain recoverable. Retained generated summaries must preserve any evidence needed after release. `remix.tools` retains its existing sequential groups of parallel calls. Custom tools use the existing registry and compiler. `duration-ticks` and `delivery` are accepted in content-first tool plans, with tool-specific semantics: the calculator completes immediately; file latency is configured by `hardware_load_set`; timers and synthetic subagents implement their own delays. An optional `outcome` is allowed only for an externally authored synthetic subagent reply, as in the existing simulator. Never put a fabricated result in a generated request's arguments.

`remix.setup` supports files in `idea.files` and, in content-first mode, optional invisible `operations: [{tool, arguments}]` for environment conditions such as file latency, web state, permission changes, or failure injection. These are external operations with no model loss. They occur at the current frontier and cannot rewrite earlier world state. The original idea and success criteria are retained as `metadata.concept`.

Use `remix.message` for user dialogue, and `remix.draft` for generated reasoning and responses. Assistant replies are delivered through generated `chat` requests and masked external chat writes. Final publication closes remaining windows through generated EOS. BOS, implicit NOOP, causal masks, tool result scheduling, and lifecycle validity remain owned by the existing compiler. Review stages receive a closed draft while generation checkpoints retain continuable open windows.

## Fast user-scenario variation

Select **`user_scenario_noval`** to generate the same class of samples as `user_problem` (`#user_scenario`) with no local validation. It inherits the original system prompt, all domain/difficulty pools and weights, user/Kidra roles and traits, idea-generation requirements, user-message prompt, schemas, and semantic review checklist. Manual selections and randomized inputs are identical in the dashboard. Future changes to those parent input pools are inherited automatically.

The variation creates one idea and uses three bounded content requests per exchange: the fictional user message, Kidra's private planning/tool decisions, and a grounded reasoning/response/continuation object after actual observations. A one-exchange sample therefore uses **four** generation requests. There is no reference-solution request, novelty judge, separate answer writer, assessment request, validator, or local semantic repair loop. The completion flag controls whether another meaningful user exchange is needed; it never validates or approves the sample. Up to four exchanges remain supported.

Planning notes and independent tool requests start together. Public tool purposes and complete argument constraints are supplied in a compact contract catalogue; redundant field documentation and OpenAI wire wrappers are omitted. No tool or executable constraint is silently truncated. Automatic reasoning-window IDs and lifecycle operations require no model-generated syntax. Results remain actual simulator observations, and external review/Ready approval remain separate from generation.

The scenario uses `extends: user_problem`. Omitted top-level input sections inherit; schemas merge by name. An explicit `steps` block defines the variation's scaffold, and `inherit: true` imports a named parent step before applying its field overrides. Prompt files fall back to the parent's saved prompt contents. Parents must be registered inside the configured scenario root; cycles, unknown parents, and unrelated duplicate tags are rejected. Only scenarios in the same inheritance family may share a tag.

Both variants retain `#user_scenario` for analysis and distribution tracking. `metadata.scenario` distinguishes the exact generator and `metadata.scenario_family` identifies the parent family. MCP uses that provenance and the saved original checklist; legacy tag-only samples continue resolving to `user_problem`. Existing datasets and unrelated scenario tags retain their original behavior.

Launch the existing dashboard with `--scenarios-dir examples/scenarios`, configure the inference tokenizer, then select `user_scenario_noval`. The run API accepts `{"scenario":"user_scenario_noval"}` and the same optional manual tags, batch size and concurrency settings as `user_problem`. No validator is contacted. When the original scenario is loaded too, the existing settings checks still require its validator alias; it may point at the same server and remains unused by the fast variation.

## Idle-work scenario without a user

Select **`idle_scenario_noval`** (`#idle_scenario`) for autonomous TODO processing,
project review, memory organization and alarm follow-up. It has only a Kidra role
and uses a bounded observe–decide–act loop rather than user/assistant exchanges.
The model chooses actions, retained private state, context releases and a quiet
or blocked ending from actual observations. No local semantic review is invoked.
See [configuration, coverage and offline demo](idle-scenario-generation.md) and the
[PDF flow chart](idle-scenario-flow.pdf).

## Parallel construction demonstration

The bundled `examples/scenarios/content_first` scenario generates a brief, independently generates two reasoning streams and tool decisions, runs the streams and tool requests together, then generates a reaction from real observations. The local model never authors replay structure. It exercises arithmetic discrepancies, missing evidence, tool failures, and user-facing delivery. Tags vary the domain and complication; model outputs determine quantities, facts, tools, and response content.

```sh
trajectory-remix init --dataset work/content.sqlite --tokenizer examples/demo-tokenizer.json
trajectory-remix --dataset work/content.sqlite --scenarios-dir examples/scenarios
```

Configure the generator as above, select `content_first`, and run it in the existing dashboard. The same action is available through `POST /api/scenarios/run` with `{"scenario":"content_first","max_parallel":3}` and the dashboard's CSRF token. Existing scenarios can opt in with one flow field, though large inherited inputs may need explicit selection or a larger request budget.

## Recover and review externally

Every request is journaled before inference; completed text, partial streamed text, reasoning when exposed, provider usage, tokenizer identity/hash, bounds, and request state are retained before schema parsing. Invalid JSON and schema-invalid outputs therefore survive. Construction failures retain the attempted replay and inputs before rolling back to the last compilable builder state. Dataset drafts retain original requirements, failures, request provenance, and deterministic diagnostics in `metadata.generation_record`. Independent SQLite generation jobs retain checkpoints even when draft publication itself fails.

Cancellation retains completed siblings and available partial text. Pause lets active requests finish; resume reuses successful steps. Process restart marks a running job interrupted, and the existing resume action restores its checkpoint. Model streams aborted by a hard process kill can lose the tail since the last periodic journal checkpoint; already persisted text remains available. Completed HTTP response parsing and persistence cannot be atomic with an external inference service.

Use the existing MCP connection, with no local inference running:

1. `get_generation_record(sample_id)` reads original flow/prompts, request summaries, and failure summaries. Select `request_index` to retrieve raw output/reasoning; set `include_failures: true` to retrieve uncompiled construction proposals. These records remain addressable after subsequent generation attempts and without scenario files.
2. `review_trajectory` / `get_review_checklist` expose the saved original scenario checklist. Inspect replay, windows, tools, and sandbox evidence.
3. `repair_trajectory` or existing replay editing commands repair the saved draft. Raw failed proposals are evidence, not executable replay. Import useful content from them into a valid replay. Repairs do not call a model and do not change the original requirements.
4. Reread the review and submit `record_trajectory_review` against its current revision and original checklist hash. Existing explicit Ready approval is still required for export.

A construction failure can depend on a schema-valid but semantically unusable cached response. Resuming deliberately reuses that response; external replay repair is the recovery path. Schema-invalid or interrupted request steps are retried on resume. Failed construction never triggers an automatic local semantic repair loop. If a trajectory is edited externally, the existing resume conflict guard prevents silent overwrites.

## Analytics and comparison

`GET /api/generation/analytics` and MCP `get_generation_analytics` aggregate current dataset candidates by mode: retained, constructed, unsuccessful, pending, externally repaired, approved, requests, raw proposals, token counters, inference time, elapsed time, and candidate/usable throughput. Content-first provenance additionally stores per-request speed, bounds/utilization, provider-vs-estimated token sources, summed inference time, union of concurrent request intervals, and generation overhead. Completion tokens include reported reasoning; reasoning is never added twice. Unknown provider counters remain estimates, not hardware measurements.

Elapsed time includes orchestration and pauses within an attempt but excludes time between stopped attempts and external review. Inference seconds measure occupied request wall time, including provider latency, transport, and request bookkeeping; OpenAI-compatible APIs expose no standard GPU compute-time counter. Inference request durations may overlap, so their sum can exceed elapsed time; `inference_wall_seconds` measures their interval union. Dataset throughput sums recorded candidate elapsed times and is intended for equivalent workload comparisons, not server-wide concurrent-session capacity. Usable throughput requires actual Ready approvals. Legacy datasets without instrumentation are not backfilled with invented counters. Repair counts cover the explicit MCP `repair_trajectory` operation; arbitrary manual edits are not classified as repairs.

Run a reproducible offline integration comparison:

```sh
python scripts/benchmark_content_first.py --samples 3 --output-dir work/content-first-demo
```

It writes two SQLite projects, inspectable samples, and `report.json`. The offline fixture uses scripted content, a local fixture inference tokenizer, and **explicitly simulated inference latency**, so its timings are not local-model/GPU benchmarks. By default it compares `user_problem` with `user_scenario_noval`, sharing generation input pools and fixture content. `--scenario-family content-demo` retains the earlier parallel-construction comparison. The existing YAML pipeline already separates much content from replay syntax; this benchmark isolates bounded assembly and deferred validation. See [recorded results](content-first-benchmark.md).

For actual endpoint measurements, use a fresh directory:

```sh
python scripts/benchmark_content_first.py --live --samples 3 \
  --base-url http://127.0.0.1:8080/v1 --model your-local-model \
  --inference-tokenizer /models/your-local-model \
  --training-tokenizer /models/kidra/tokenizer.json \
  --output-dir work/content-first-live
```

Both modes use the same tag seed and temperature zero. Live outputs can still differ; compare original criteria, quality, diversity, repairs, and approval yield alongside inference savings. The tool reports reductions only when both modes construct the requested candidate count. It never grants training approval.

## Remaining limits

- Causal scaffolds are scenario-defined; model decisions can vary calls, content, branches, and repeats. The assembler does not invent reasoning or autonomously discover new scenario topologies. Richer domains need additional scenario graphs and externally reviewed requirements.
- Each assembly stage waits for its committed batch before requesting reactions. It supports overlapping replay streams and tools, but does not perform incremental per-token LLM intervention into an uncommitted batch.
- Oversized required inputs fail explicitly. Authors must divide evidence into meaningful bounded requests; there is no automatic lossy summarization or unbounded-context fallback.
- The inference tokenizer/template and total-completion parameter must match the server. Other than returned usage, OpenAI-compatible APIs do not provide a portable way to attest a remote server's hidden tokenization or reasoning accounting.
- Full semantic quality, diversity, and usable training throughput need external review of real model outputs. The offline demonstration establishes construction/recovery behavior and cost accounting, not semantic equivalence of different local models.
