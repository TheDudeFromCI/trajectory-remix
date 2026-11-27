# Project storage

This page explains what a Remix project file contains, what it doesn't, and how to back it up safely. You never need to open
the database yourself: use the studio, [REST or MCP](MCP.md), which enforce the rules below.

## What's in a project

A project is one SQLite database in WAL mode (format `kidra-graph-project/2`). It holds:

- the current head of every trajectory, plus **immutable revisions** of each source,
- the current project settings, split into tables, plus snapshots of earlier settings while something still uses
  them (see [How settings are stored](#how-settings-are-stored)),
- an index of which trajectories use which tool,
- job state: ownership, FIFO request queues, frozen run inputs and committed checkpoints,
- reviewer comments and their replies, the last finished Assistant conversation for each trajectory, and the
  Assistant's pending proposed changes (a working copy and its base revision). None of these is part of a source, so
  they never change its revision or export; a proposal changes the source only when someone applies it.

Edits use compare-and-swap on a per-trajectory revision, so an edit to one trajectory never invalidates a save to another.
A pipeline that owns a trajectory (generation, or a manual review) holds it exclusively, and other edits wait for it.

## How settings are stored

Current settings are kept in tables, not as one document:

| Table | Holds |
| --- | --- |
| `graph_tokenizer_definitions` | Each tokenizer JSON file, stored once and keyed by a hash of its contents |
| `graph_tokenizer_profiles` | Profile name, the hash of its tokenizer file and the control-token map |
| `graph_settings_endpoints` | One row per model endpoint |
| `graph_settings_endpoint_roles` | Which endpoint serves Draft, Assembly, Fast Review, Critical Review and Assistant |
| `graph_settings_tools` | One row per tool schema |
| `graph_settings_values` | The smaller sections: prompts, review checklists, generation options, size labels and so on |

Each save also records a small snapshot of the settings in `graph_settings_revisions`. A snapshot refers to
tokenizers by hash, so a large tokenizer file is never copied. Snapshots are deleted once nothing needs them. A
snapshot is kept when it is:

- one of the 20 most recent saves, so a save made from a settings dialog opened a little earlier can still be
  merged with other people's changes;
- the settings that a trajectory's latest revision was saved with;
- the settings a job started with, for as long as the job is listed. Jobs keep their frozen configuration. Each
  job's settings revision is recorded in `graph_settings_pins`, and the record goes when the job is cleared.

A tokenizer file is deleted when no profile and no kept snapshot uses it.

Projects saved by earlier versions (format `kidra-graph-project/1`), which kept a full copy of the settings for every
save, are upgraded to `kidra-graph-project/2` the first time they are opened. The upgrade then compacts the file, which
can take a few seconds for a large project. Older versions of Remix refuse an upgraded project, so back it up first if
you might need to open it with one.

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
