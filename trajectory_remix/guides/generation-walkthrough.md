# Scenario generation walkthrough

This report focuses on LLM input/output. Each model call includes its exact rendered messages, output schemas and constraints, raw response, provider-returned reasoning when available, token usage, timing and errors. Responses appear once per call. Messages retain any trajectory context actually sent to the model; raw trajectories, compiler results and sandbox snapshots are omitted elsewhere. Configured endpoint credentials are redacted.

Attempt boundaries, step timings, conditions, retries, fallbacks and skips explain the control flow. Paired start/finish IDs identify overlapping calls. The final status distinguishes completion, rejection, cancellation and a provider error that pauses a resumable draft. Reported model time is summed across calls and can exceed wall time when calls overlap.

This runs normal generation: models propose content, remix steps assemble the sample and execute simulated tools, and later calls receive the selected visible context and tool feedback. Scenario iteration limits apply per attempt; a batch targeting one completed sample can retry failed attempts up to its configured attempt limit. Compiler/tool checks and checkpoints run during ordinary generation too. The report adds no model calls or tool executions and does not collect compiler or workspace snapshots.

Request budgets are enforced when an inference tokenizer is available; skipped budget checks are identified per call. JSON/schema and compiler/tool contracts are checked during creation. Semantic review remains a separate Validate Trajectory operation; saved review checklists are only model input when included in the rendered messages.

Drafts, checkpoints and ordinary generation records are saved normally. This walkthrough exists only for this request and is never stored in the database.
