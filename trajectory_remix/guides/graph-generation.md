# Generate or review graph sources

Read `get_graph_session` first. The Draft endpoint must be configured
for `generate_graphs`; Assembly and the review roles are optional. Requests invoke those endpoints.
Confirm that model use is within the current task authorization.

Call `generate_graphs(requests=[{"seed":137,"overrides":{"domain":"software_engineering"}}], instructions={})`
for one attempt. Pins are overrides, separate from weights; any category can be pinned, and pins
the matrix's rules contradict are refused with the reason. Inputs, settings,
checklists and proposals freeze before any attempt starts. Inspect the returned
job; pause/resume/cancel remain explicit lifecycle controls. No replacement
attempt is created for failure or detachment.

One existing trajectory: `draft_graph(id="UUID",expected_revision=REV)` writes a new draft
(metadata.draft) from a proposal that keeps the trajectory's set category, label and target
metrics and samples the unset ones; `assemble_graph(id="UUID",expected_revision=REV)` then edits
its current graph in place to match the draft. Both are Draft/Assembly model jobs.
`sample_graph_metrics` fills unset metrics the same way without any model call.

Read a detached source and its revision before
`review_graphs(trajectory="UUID",expected_revision=REV,mode="standard")`.
Each review repairs what fails, then signs off. Standard reviews Pending with Fast if assigned,
otherwise with Critical. It reviews Candidate with Critical and failed stages again at their tier. Other stages skip. Use `force_fast` or
`force_critical` only when requested. Locks cannot be overridden. Search-wide
review uses `query` instead of a selected UUID and freezes all pages.

Review evidence is bound to source/checklist/model identities. Source/checklist
edits stale signoffs without resetting stages or automatically calling models.
Malformed/provider failures are not quality verdicts: a trajectory that needs manual
intervention is held while the rest of the job continues. Endpoint exhaustion and
server restart require manual Resume. A review is one continuing tool conversation.
An unfeasible signoff fails the review; a context limit or the exchange cap ends it with
no verdict. Either way its partial edits are saved.

Generation and review never token-compile or execute synthetic tools. Prepared
local-model checks are described in `docs/GRAPH-LOCAL-LLM.md`; do not run live models
or benchmarks without task authorization.

Generation is Draft → Assembly → Fast Review → Critical Review. Draft writes prose
working notes. Assembly edits the graph with native tools and signs off after every
error and enabled warning is resolved. The prompts (agent prompt, authoring prompt, Draft and
Assembly instructions) are project settings (`settings.prompts`) and freeze with the run;
`instructions` takes no keys. See `docs/GRAPH-GENERATION.md`.
