# Review semantics before signing off

You are assessing the behavior being taught, including task correctness and
Kidra's context dependencies. A successful compile with zero warnings establishes
neither. Read this guide before reviewing or repairing generated trajectories.

## Scenario checklist and MCP tools

Find samples with `list_trajectories(query="@state:candidate #user_scenario", limit=5)`.
The bundled flow emits `#user_scenario`; custom flows choose their `tag` in
flow.yaml (default: folder name). Randomized scenario and `role:trait` tags remain.

Call `review_trajectory(sample_id=...)`. It automatically fetches the matching
checklist, repair guidance, this guide, source, compiler diagnostics, `revision`
and `checklist_hash`. Use `include_source=false` for large samples, then inspect
bounded events/windows/snapshots. `get_review_checklist` fetches only the contract.
No model endpoints run; these are MCP authoring tools, not replay tools.

Generated `metadata.review_checklist` snapshots preserve original rules/prompts
after export/import or scenario-file changes. Older/tag-only samples need the
matching `--scenarios-dir`; metadata.scenario is a legacy fallback. Missing or
conflicting scenarios fail explicitly. Never substitute another scenario's rules.

Evaluate every rule yourself over the whole trajectory, including all groups.
Give concise evidence before each status and cite event IDs for failures.
Explain permitted `not_applicable` results. Resolve missing evidence before
recording; `needs_context` cannot approve a sample.

Use `repair_trajectory` with the revision/checklist hash and `edits` referencing
original `replay[tick][index]` IDs: replace/delete, insert_before/insert_after,
append, or replace_text with a JSON-pointer field and exact unique old/new text.
The candidate compiles before one atomic commit; `dry_run=true` validates without
saving. Repairs cannot edit metadata/identity. Reread `review_trajectory` afterward
and review every rule again.

Finish with `record_trajectory_review`: pass the latest revision/checklist hash,
your reviewer/summary, and `results` matching `checklist.results_schema`.
Include every category (e.g. `validate.reasoning`) and every rule's
`evidence`, `status`, `feedback`; passing feedback is empty. Code validates
coverage and computes state 2/-2. This records your assessment, not independent
proof, and never Ready (3). Stale revisions/checklists require rereading.
For manual/non-scenario samples use the general guidance and `sign_off_trajectory`.

## 1. Establish what Kidra can know

Kidra attends to **all currently open Kidra windows** through shared global
attention. It does not need a tool call to read another open window. Closing a
window drops its contents from Kidra's active context: the attention mechanism
can no longer see or use that lost context, including while generating later
tokens of an ongoing stream. Historical replay records do not preserve access
for Kidra. Only information already retained in another open window remains
available. An idle open window retains context.
Replaced content is also unavailable unless preserved elsewhere.

Invisible sandbox files are not visible merely because they exist. File tools
can reveal them. Subagents have their own scoped conversations/artifacts; do not
assume they inherit all of Kidra's open windows.

For each important claim, identify the supporting facts, when they became
available, and which windows retain them. Check the original task and artifacts,
not only a generated solution or validator's previous feedback.

## 2. Justify each tool call

Ask: what information or action does this call add?

- `file_read` of an unchanged file already fully visible in an open attachment
  is redundant unless the request needs additional evidence, such as a new version.
- `file_list` needs a reason to discover directory contents or metadata; do not
  list merely to rediscover known attachment paths.
- Reading an unseen file or checking an actual changed version can be necessary.
  Deleting such a read would remove evidence, not improve efficiency.
- Verify arguments, outcomes and downstream conclusions agree. Do not accept
  invented tool observations, unsupported claims, or unnecessary serial calls.

## 3. Check context throughout generation

A stream's start tick is not its completion. Check evidence availability across
the **whole dependent stream**, including reasoning and composition of a `chat`
tool request. Closing a source at the start of an answer can strand its later
tokens. Finished tool execution does not itself mean its evidence is no longer
needed.

Use `get_anchors`, `get_window_history`, bounded `preview_snapshots` and
`get_sandbox_state` to inspect actual timing: before consumption, during the
stream, at completion and at window closures/replacements. Use anchors for repairs;
join independent completions with `max(...)` where needed.

For example: attachment A supports reasoning B, which supports chat request C.
If B retains every fact C needs, A may close after the transfer into B finishes.
B must remain available until C no longer needs it. If B merely says "use A",
closing A loses that evidence. Close both after their last actual use; do not
keep all windows open until the final reply by default. Document the dependency
that justifies each nontrivial closure.

## 4. Look for performance improvements

Use multiple reasoning windows for independent analyses, such as checking one
file's logic, another file's API contract, and the user's constraints. Overlap
their streams when the required inputs are available; each generating window can
emit a token in the same tick. Do not serialize unrelated reasoning through one
window. Dependent conclusions must still wait for their supporting facts; join
the necessary completion anchors with `max(...)` before combining results.

Extract relevant facts from large documents into a smaller reasoning or evidence
window. Preserve the exact details downstream work needs: constraints, values,
quotes, exceptions, and source/version references where relevant. Once that
transfer finishes and no consumer needs the original document, close its window
to reduce active context and attention workload. A reference to a closed document
does not make its contents available, and an incomplete summary can break later
reasoning. Check that the retained information really supports the final answer.

For example, extract a configuration's three relevant settings and their caveats,
then close the long configuration window while reasoning from those settings.
Retain the original if later work still requires exact text omitted from the
extraction. Close temporary analysis windows after their last consumer finishes.

These are opportunities to reduce serial ticks and active context, not guaranteed
wall-clock speedups. More windows also add overhead. Prefer useful concurrency
and compact evidence over redundant analyses or summaries that duplicate an
entire document without allowing earlier closure.

## 5. Check the intended training behavior

Verify the solution independently against the problem and supplied evidence.
Review user knowledge, claims, uncertainty, clarifications and final answer for
consistency. Check that independent reasoning/work overlaps where useful.
Attachments belong in separate windows opened with the user's message. Tool
schemas belong in a visible tools window before calls. Kidra sends messages via
the `chat` tool; generated chat-window prose is not a delivered message.

## 6. Repair, reread, then sign off

Make targeted edits, rerun `validate_trajectory(semantic=false)` and inspect its
diagnostics. Recheck semantic dependencies affected by the edits; moving a closure
or deleting a tool can break later claims even when compilation still passes.
Reread and review the final source at the revision you will sign off.

For scenario samples, use `record_trajectory_review` as described above.
For manual samples, call `sign_off_trajectory` with verdict `validated` or `rejected`, your reviewer
label, a specific summary of checks/findings and the reviewed `expected_revision`.
Support the summary with concrete event/window/anchor references when relevant.
Do not claim checks you did not perform. If evidence is missing or uncertain,
report that limitation and do not sign off as validated until it is resolved.

Compilation and sign-off make no model API calls. `semantic=true` on
`validate_trajectory` invokes configured model endpoints; use your own reasoning
for subscription-backed review. Sign-off records your assessment at 2 or -2;
it does not run another reviewer. Later edits invalidate it. Human Ready (3)
remains separate.
