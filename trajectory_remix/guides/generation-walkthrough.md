# Scenario generation walkthrough

This report follows the normal generation pipeline. Drafts, checkpoints and ordinary generation records are saved normally; this walkthrough is request-scoped and is never stored in the database.

Events are ordered by observation time. Start/finish event IDs identify overlapping requests and nested steps. Step definitions include dependencies, conditions, retries and fallbacks. Model requests contain the exact rendered messages and original JSON schema, plus the provider decoding schema and local constraints. Prompt sources are listed separately: a source or saved review checklist is only shown to a model if it appears in that request’s messages.

Generation enforces request budgets when an inference tokenizer is available, JSON parsing/schema checks and compiler/tool contracts. Semantic review is deferred to Validate Trajectory; saved review checklists are not automatically enforced during creation. Budget skips, failures and compiler diagnostics appear below.

Model responses do not apply workspace edits themselves. Subsequent remix steps construct the trajectory and execute simulated tools. Workspace changes use JSON pointers with before/after values. Compiler results include each tool call and the sandbox state and changes at every recorded execution tick; simultaneous changes share a tick. Compiler results may include earlier calls replayed or cached by the normal pipeline. No extra tool execution is performed for this report.

Reasoning is included only when returned by the provider. Missing reasoning is marked unavailable. Token metrics retain their reported/estimated source. Configured endpoint credentials are redacted.
