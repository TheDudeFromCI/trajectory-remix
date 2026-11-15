# Public source format

The only authoring format is `kidra-trajectory-graph/1`.
See [Graph source](GRAPH-SOURCE.md) for fields, examples, masks, fragments and
structural diagnostics; `/api/v1/schema/source` exposes its generated JSON schema.
[KTREMIX](KTREMIX.md) is the compiled training container, not an authoring source.

Replay maps, tick anchors, executable sandbox and migration readers have been
removed. Old project databases fail explicitly on opening. Imports skip invalid
records with per-item logs and do not infer a graph from old source.
