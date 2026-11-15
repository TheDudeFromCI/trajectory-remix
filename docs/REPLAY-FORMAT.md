# Source and execution formats

The old replay/tick-anchor format is retired. Author
[`kidra-trajectory-graph/1`](GRAPH-SOURCE.md) source graphs and use
[the graph compiler](GRAPH-COMPILER.md) for explicit Tick history or export.

Raw source exports use JSONL. Compiled training shards use
[KTREMIX](KTREMIX.md), with independent compressed trajectories and indexed
random access. No old-format reader or migration is provided.
