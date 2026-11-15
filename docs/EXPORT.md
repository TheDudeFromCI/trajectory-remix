# Export

Export turns your trajectories into files you can train on or share. You can run it from the
studio's **Export** dialog or the `export_graphs` MCP task. Both use the same asynchronous service.

## Choose a kind of export

| Kind | What you get | Needs a tokenizer? |
| --- | --- | --- |
| **Source JSONL** (raw) | The authored graphs, one per line. Portable and re-importable. | No |
| **Compiled KTREMIX** | Compressed training shards, via the [compiler](GRAPH-COMPILER.md). See [KTREMIX](KTREMIX.md). | Yes: choose a [tokenizer profile](PROJECT-SETTINGS.md#tokenizer-profiles) |

## Options and defaults

| Option | Default | Notes |
| --- | --- | --- |
| Minimum stage | **Ready (3)** | `None` includes sources without a stage. |
| Include stale | on | Stale reviews don't change a stage. Turn off to exclude them. |
| Exclude missing split | off | Missing splits warn either way. |
| Trajectories per file | 256 | Whole trajectories only. |
| Metadata | keep targets and measured metrics | See projection policies below. |

Compilation settings such as active-window capacity and seed are pinned in the export, so the
same inputs give the same output.

## What comes out

Each export runs as a job, writing to its own directory under `/tmp`:

- the shards, one split per shard (raw JSONL or `.ktremix`),
- the exact tokenizer and profile used (for compiled exports),
- a manifest with file lengths and SHA-256 hashes,
- a ZIP of everything.

Downloads become available only after the whole bundle is complete. A failed or cancelled export
removes its directory, partial shards included, so you never see a half-finished result. Each shard
has its own CRC and bounds checks.

> **Downloads are temporary.** They expire 24 hours after completion, and the operating system may
> clean `/tmp` sooner. Finished job records stay, but an expired file returns 404. Download what you
> need, since exporting never rewrites your sources.

Downloads need the dashboard's session token. MCP returns file paths, never tokens or archives.

## Splits and families

Splits are `Train`, `Development` and `Test` (lowercase also works), and **Auto** is a deliberate
choice. Export resolves them without changing your sources.

- A **family** (`family_id` in metadata) is never split apart. If members have conflicting explicit
  splits, the export is blocked.
- Auto and missing-split families are assigned to whichever split is furthest below its target ratio,
  after counting explicit assignments. Ratios can end up approximate.
- Each shard contains one split. The split seed is recorded separately from the allocation seed.

## Metadata projection

Choose how much metadata the export keeps. Required compiler inputs always survive, and compiled arrays
never change.

| Policy | Keeps |
| --- | --- |
| `full` | Everything |
| `all_metrics` | `metrics` and `targets` |
| `measured_metrics` | `metrics` |
| `remove_all` | Nothing optional |

Intrinsic data is always kept in raw JSONL.

## From code

```python
from trajectory_remix.graph.export import ArtifactService, ExportOptions

artifacts = ArtifactService()  # sweeps expired and abandoned exports on startup
artifact_id, manifest = artifacts.build(
    records, settings, compiler=compiler,
    options=ExportOptions(trajectories_per_file=100, metadata="remove_all"))
with artifacts.lease(artifact_id, "download.zip") as stream:
    consume(stream)  # stream to the client while holding the lease
```

`records` accepts sources, dicts or `SourceRecord` snapshots, copied before selection. Revision
identities, hashes, settings, profile, compiler, capacity and seed are pinned in the manifests. You
must supply a consistent snapshot from the real job service, because this API doesn't take job
ownership. Directories are mode 0700. `expire()` suits a periodic sweep, and writer and download locks
protect active files. See [Jobs](GRAPH-JOBS.md#logs-and-artifacts).

## Learn more

[Compiler](GRAPH-COMPILER.md) for profiles and timing, [KTREMIX](KTREMIX.md) for the file format,
[Jobs](GRAPH-JOBS.md) for REST controls, and [MCP](MCP.md) for task examples. The export menu in the
studio is documented in the [Studio reference](GRAPH-STUDIO.md#import-export-and-jobs).
