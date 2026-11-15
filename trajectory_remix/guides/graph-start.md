# Manual graph authoring

Use `get_graph_session` once, then `list_graphs` with a bounded limit. `read_graph`
returns one source, its revision, diagnostics and owner. Start a detached draft
with `create_graph`, using format `kidra-trajectory-graph/1`, a new UUID, logical
`windows` and `events` maps. No tokenizer is required.

Use `patch_graph` or `patch_graph_event` with `expected_revision` from the last
read. Conflicts preserve your draft: reread and reconcile explicitly. Errors in
an otherwise valid graph DTO remain editable; structural errors block preview
and compiled export. Source edits commit immediately, independent of the browser.
Metadata is optional and cannot change compilation. Stage Ready is a manual
choice, never proof of quality from saving a graph.

Read one next topic: `dependencies`, `tools-chat`, or `export`. Definitions and
JSON schemas are in Project Settings and `/api/v1/schema/source`. Manual editing never executes tools or calls a model. The `generation` guide
describes explicitly authorized model work and its independent jobs.
