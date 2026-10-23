# Six-specialist coordination benchmark

The original user prompt is stored in `metadata.benchmark_prompt` in
[`examples/coordination-benchmark.json`](../examples/coordination-benchmark.json).
The software task is a standard-library Python slug generator with a CLI.
Kidra coordinates six actual external Codex workers: core, CLI, tests, docs,
API review and integration review. Specialist responses were returned by those
workers through MCP, rather than authored in the coordinator script.

The saved [validation report](coordination-benchmark-report.json) records:

| Check | Result |
|---|---|
| Distinct conversations / workers | 6 / 6 |
| Conversations with clarifying follow-ups | 6 |
| Completed jobs | 14 |
| Completed task notes | 6 |
| Coordinator file writes | 0 |
| Passing Docker executions | 22 |
| Final fresh unittest discovery | 36 tests passed, exit 0 |
| Compiler diagnostics | None |
| Model windows | 2, both closed with EOS |
| Compiled rows / final tick | 115,105 / 18,196 |

The API reviewer added ten independent tests to the test specialist's 26 tests.
Both reviewers received the actual implementation, tests and README as pinned
references. They resumed once more after documentation changes to verify the
final handoff. One recorded nonzero execution intentionally exercised CLI error
handling; it was not a failed job or a failing final suite. Execution receipts
retain exact inputs, image IDs, outputs and statuses.

The coordinator script contains requirements and orchestration, not the slug
implementation. Worker `file_write` calls change private workspaces. Completing
a job publishes those files as invisible events, preserving implementation
provenance without training Kidra to write it. Root status/chat calls record
observed queued/running/completed counts. Explicit result retrieval captures
specialist answers before the final handoff.

The project was restarted after completion. MCP validation and historical
snapshot inspection succeeded with no live tool sessions, and the dashboard
replayed the 115,105-row trajectory through its compact preview path. Export and
replay do not execute code or contact a provider.

## Reproduce

Install `.[mcp,test]`, Docker and a Python image. Start an initialized project:

```bash
docker pull python:3.12-slim
python -m trajectory_remix init --dataset work/benchmark.sqlite --tokenizer examples/demo-tokenizer.json
python -m trajectory_remix --dataset work/benchmark.sqlite --port 8766 --execution-backend docker
```

From another terminal:

```bash
python scripts/coordination_benchmark.py launch --work work/benchmark-state
```

Attach six actual workers. Each worker uses its own stable identity and role
(`core`, `cli`, `tests`, `docs`, `api_review`, `integration_review`):

```bash
python scripts/benchmark_worker.py claim --role core --worker-id worker-core --work work/benchmark-state
python scripts/benchmark_worker.py tool --role core --worker-id worker-core --work work/benchmark-state --input calls.json
python scripts/benchmark_worker.py complete --role core --worker-id worker-core --work work/benchmark-state --input response.txt
```

`calls.json` contains `{name, arguments}` or an array of worker tool requests.
The worker implements its delegated task and writes that JSON; the benchmark
driver never substitutes recorded answers for a live worker. Files and tests
run inside the runtime. Scratch JSON files do not publish outputs until the
worker invokes its MCP tools.

Once initial work completes, run `coordination_benchmark.py followups` with the
same `--work`. Resume the same workers with `benchmark_worker.py --stage followups`
on every claim/tool/complete command. Run `refresh-reviews` for the final docs
handoff and use `--stage final_reviews` for the two reviewer conversations.
`status` records runtime-driven progress messages throughout.

Finish with:

```bash
python scripts/coordination_benchmark.py finish --work work/benchmark-state \
  --dataset work/benchmark.sqlite --output work/benchmark-output
```

This retrieves real results, runs fresh isolated tests, closes TODOs/windows,
validates the coordination contract, and saves JSON source, validation evidence
and a SQLite backup. `export` repeats only inspection/backup of an already
finished run. To inspect the checked-in run without workers or Docker, import
its JSON into any project using the demo tokenizer and validate through MCP.

External worker identities and checksums are recorded provenance, not signatures
or a proof that arbitrary prose claims are true. Native configured workers are
also supported; their provider/tool integration is regression-tested with a fake
provider, while this benchmark used the six actual external Codex workers.
