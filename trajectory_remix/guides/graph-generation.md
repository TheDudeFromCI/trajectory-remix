# Generate or review graph sources

Read `get_graph_session` first. The Generator endpoint must be configured for
`generate_graphs`; review roles are optional. Requests invoke those endpoints.
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
Malformed/provider failures are not quality verdicts. Endpoint exhaustion and
server restart require manual Resume. Repair is one continuing session, preserving
spot patches; failure/context exhaustion ends detached Unfinished.

Generation/review/repair never token-compile or execute synthetic tools. Actors
see only causal open windows. Automatic chat echo can be disabled per call for
explicit filtered/rejected outcomes. Prepared local-model checks are described
in `docs/GRAPH-LOCAL-LLM.md`; do not run live models or benchmarks without task
authorization.
