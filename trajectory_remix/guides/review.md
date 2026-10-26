# Review semantics before signing off

You are assessing the behavior being taught, including task correctness and
Kidra's context dependencies. A successful compile with zero warnings establishes
neither. Read this guide before reviewing or repairing generated trajectories.

## 1. Establish what Kidra can know

Kidra attends to **all currently open Kidra windows** through shared global
attention. It does not need a tool call to read another open window. A closed
window is removed from attention; the reviewer seeing its historical contents
does not mean Kidra still sees them. An idle open window retains context.
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

## 4. Check the intended training behavior

Verify the solution independently against the problem and supplied evidence.
Review user knowledge, claims, uncertainty, clarifications and final answer for
consistency. Check that independent reasoning/work overlaps where useful.
Attachments belong in separate windows opened with the user's message. Tool
schemas belong in a visible tools window before calls. Kidra sends messages via
the `chat` tool; generated chat-window prose is not a delivered message.

## 5. Repair, reread, then sign off

Make targeted edits, rerun `validate_trajectory(semantic=false)` and inspect its
diagnostics. Recheck semantic dependencies affected by the edits; moving a closure
or deleting a tool can break later claims even when compilation still passes.
Reread and review the final source at the revision you will sign off.

Call `sign_off_trajectory` with verdict `validated` or `rejected`, your reviewer
label, a specific summary of checks/findings and the reviewed `expected_revision`.
Support the summary with concrete event/window/anchor references when relevant.
Do not claim checks you did not perform. If evidence is missing or uncertain,
report that limitation and do not sign off as validated until it is resolved.

Compilation and sign-off make no model API calls. `semantic=true` on
`validate_trajectory` invokes configured model endpoints; use your own reasoning
for subscription-backed review. Sign-off records your assessment at 2 or -2;
it does not run another reviewer. Later edits invalidate it. Human Ready (3)
remains separate.
