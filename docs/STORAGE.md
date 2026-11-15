# Project storage

An initialized graph project is a SQLite database using WAL. It contains current
trajectory heads, immutable source/settings revisions, tool-usage indexes, job
ownership, FIFO requests, frozen run inputs and committed checkpoints. Source
edits use revision compare-and-swap; an unrelated source edit does not invalidate
a save. Attached generation and manual review hold exclusive source ownership.

Use the [shared REST/MCP services](MCP.md) rather than writing tables directly.
A database copied for backup should be taken with SQLite's backup API or after
the server has closed; copying only a live main file can omit WAL changes.

Browser Preferences and bounded Undo/Redo history are local to that browser.
Project settings and source revisions remain server-side. Clearing the log cache
never removes source, checkpoints, signoffs or ownership. Runtime credentials are
not stored in project records. See [jobs](GRAPH-JOBS.md) and
[project settings](PROJECT-SETTINGS.md).

Source JSONL exports are portable authored graphs; compiled `.ktremix` shards are
training artifacts. Downloads and ZIPs are temporary `/tmp` files with 24-hour
expiry and can also disappear through OS cleanup. Exporting does not rewrite the
source's split or metadata. See [export](EXPORT.md) and [KTREMIX](KTREMIX.md).

Old replay/sandbox databases have no compatibility reader or migration path.
Retain an old project separately if historical data is needed.
