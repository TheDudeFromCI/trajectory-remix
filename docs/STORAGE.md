# Project storage

This page explains what a Remix project file contains, what it doesn't, and how to back it up safely. You never need to open
the database yourself: use the studio, [REST or MCP](MCP.md), which enforce the rules below.

## What's in a project

A project is one SQLite database in WAL mode (format `kidra-graph-project/1`). It holds:

- the current head of every trajectory, plus **immutable revisions** of each source and of the project settings,
- an index of which trajectories use which tool,
- job state: ownership, FIFO request queues, frozen run inputs and committed checkpoints,
- reviewer comments and their replies, the last finished Assistant conversation for each trajectory, and the
  Assistant's pending proposed changes (a working copy and its base revision). None of these is part of a source, so
  they never change its revision or export; a proposal changes the source only when someone applies it.

Edits use compare-and-swap on a per-trajectory revision, so an edit to one trajectory never invalidates a save to another.
A pipeline that owns a trajectory (generation, or a manual review) holds it exclusively, and other edits wait for it.

## What isn't in it

- **Credentials.** API keys come from the server's environment and are never stored in project records, sources or logs.
- **Browser state.** Preferences and the bounded Undo and Redo history stay in your browser. Settings and source revisions
  stay on the server.
- **Exports.** Downloads and ZIPs are temporary files under `/tmp`, kept for 24 hours and possibly removed sooner by the
  operating system. Exporting doesn't change a source's split or metadata.
- **Old formats.** Replay and sandbox databases have no reader or migration. Keep any old project separately.

Clearing the log cache never removes sources, checkpoints, sign-offs or ownership.

## Backups

Copying only the main `.sqlite` file of a **running** project can miss changes that are still in the WAL file. Take backups
using SQLite's backup API, or copy after the server has closed.

## Related

- [Jobs](GRAPH-JOBS.md): ownership, checkpoints and recovery.
- [Project settings](PROJECT-SETTINGS.md): what's stored as settings.
- [Export](EXPORT.md) and [KTREMIX](KTREMIX.md): the portable outputs. Source JSONL is a portable authored graph, and
  `.ktremix` shards are training artifacts.
