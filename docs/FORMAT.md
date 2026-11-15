# Formats at a glance

Remix uses a few formats, each for a different job.

| Format | Used for | Read |
| --- | --- | --- |
| `kidra-trajectory-graph/1` | **Authoring.** The only source format: one trajectory as a graph of events. Raw exports are JSONL of these. | [Source format](GRAPH-SOURCE.md) |
| `kidra-graph-settings/1` | Project settings: tools, endpoints, tokenizer profiles, checklists. | [Project settings](PROJECT-SETTINGS.md) |
| `kidra-graph-project/1` | The project's SQLite database. | [Storage](STORAGE.md) |
| **KTREMIX** (`.ktremix`) | **Training.** Compiled, compressed shards with indexed random access. Not an authoring format. | [KTREMIX format](KTREMIX.md) |

`GET /api/v1/schema/source` serves the generated JSON Schema for the source format.

The older replay, tick-anchor and executable-sandbox formats were removed. Old project
databases fail with a clear error when opened, and imports skip old records with per-item
logs. Nothing tries to infer a graph from old source. See
[From the old replay format](REPLAY-FORMAT.md).
