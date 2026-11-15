# Export

Use the studio Export dialog or `export_graphs` MCP task. The shared asynchronous
export service writes source JSONL or compiled KTREMIX, separate split shards,
manifest and ZIP in a job-owned directory under `/tmp`. The default minimum stage
is Ready; include stale is on, exclude missing split is off, and metadata keeps
all metrics. Required compiler inputs always survive metadata projection.

See [Compiler and export contracts](GRAPH-COMPILER.md) for profiles, split-family
allocation, trimming, CRC checks, cancellation, download leases and 24-hour expiry;
[Jobs](GRAPH-JOBS.md) for REST controls; [MCP](MCP.md) for task examples.
