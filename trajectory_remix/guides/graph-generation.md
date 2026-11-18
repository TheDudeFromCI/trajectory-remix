# Generate or review graph sources

Read `get_graph_session` first. The Draft (or Generator) endpoint must be configured
for `generate_graphs`; Assembly and the review roles are optional. Requests invoke those endpoints.
Confirm that model use is within the current task authorization.

Call `generate_graphs(requests=[{"seed":137,"overrides":{"domain":"software_engineering"}}], instructions={})`
for one attempt. Pins are overrides, separate from weights. Inputs, settings,
checklists and proposals freeze before any attempt starts. Inspect the returned
job; pause/resume/cancel remain explicit lifecycle controls. No replacement
attempt is created for failure or detachment.

Read a detached source and its revision before
`review_graphs(trajectory="UUID",expected_revision=REV,mode="standard",repair=true)`.
Standard reviews Pending with Fast only, Candidate with Critical, and repairs
failed stages at their tier. Other stages skip. Use `force_fast` or
`force_critical` only when requested. Locks cannot be overridden. Search-wide
review uses `query` instead of a selected UUID and freezes all pages.

Review evidence is bound to source/checklist/model identities. Source/checklist
edits stale signoffs without resetting stages or automatically calling models.
Malformed/provider failures are not quality verdicts: a trajectory that needs manual
intervention is held while the rest of the job continues. Endpoint exhaustion and
server restart require manual Resume. Repair continues across failing verdicts and
fresh rounds; it fails only on `give_up` or at the round cap (detached Unfinished).

Generation/review/repair never token-compile or execute synthetic tools. Prepared
local-model checks are described in `docs/GRAPH-LOCAL-LLM.md`; do not run live models
or benchmarks without task authorization.

Generation is Draft → Assembly → Fast Review → Critical Review. Draft writes prose
working notes; Assembly writes one compact spec that Remix builds into a graph, then
fixes it from validator diagnostics. The prompts (agent prompt, Draft and Assembly
instructions) are project settings (`settings.prompts`) and freeze with the run;
`instructions` takes no keys. See `docs/GRAPH-GENERATION.md`.
