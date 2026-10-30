# Search trajectories

The dashboard search box and MCP `list_trajectories(query=...)` use the same
case-insensitive query language. An empty or whitespace-only query lists all
trajectories. Search filters saved trajectories; save pending dashboard edits
before expecting them to appear in results.

## Readable text

A plain term such as `python` matches that text anywhere in a trajectory's
string values, including its name (`id`), UUID, metadata and tags, replay
content, segment text, tool arguments and authored tool outcomes. Terms are
also matched across adjacent text segments, even when a segment boundary splits
a word. Plain terms are
literal substrings: `python` also matches `Pythonic`. Unicode case folding
makes `PYTHON` equivalent to `python` and `Straße` equivalent to `strasse`.

Text search reads authored source, including invisible setup. It does not compile
or execute a trajectory, query providers, or search unrecorded runtime results.
The diagnostic properties below use local compilation instead.
JSON field names, JSON escaping, and numeric/boolean/null values are not text.
For example, a metadata key `python` alone does not match; its string value does.

## Combining terms

| Query | Meaning |
| --- | --- |
| `python` | Contains `python`. |
| `java list` | Contains both terms, anywhere in the same trajectory; they may occur in different events or fields. |
| `-complex` | Does not contain `complex`. |
| `python -(math complex)` | Contains `python`, excluding trajectories that contain both `math` and `complex`. Either excluded term alone is allowed. |
| `apple \| banana` | Contains either term, or both. |
| `(apple \| banana) -complex` | Contains either fruit term and does not contain `complex`. |
| `"java list"` | Contains the contiguous phrase `java list`. |

Negation binds first, then spaces (AND), then `|` (OR). Thus `apple | banana
python` means `apple | (banana python)`. Parentheses override precedence and
can nest. A minus sign at the start of an entry negates it; internal hyphens
remain literal, as in `parallel-window-one`. Double quotes keep spaces, `|`,
and parentheses inside a term, as in `"(example)"` or `@name:"my trajectory"`.
Quotes do not disable `*`, `#`, or `@` syntax. A quoted `"-complex"` searches
for the literal leading minus sign.

## Wildcards

`*` matches zero or more word characters in a text term. Wildcard text terms
must match at word boundaries; word characters are Unicode letters, digits and
underscore. Wildcards never cross spaces or punctuation, but punctuation
written explicitly in the pattern is literal.

| Query | Examples |
| --- | --- |
| `human*` | `human`, `humans`, `humankind`; excludes `superhuman`. |
| `a*e` | `apple`, `alone`; excludes `apples`. |
| `*math*` | Any word containing `math`. |

Only `*` is a wildcard. Regex operators such as `.`, `+`, `?`, and `[abc]` are
literal text. In properties and tags, `*` can span any characters, including
spaces and punctuation, and the pattern matches the entire property/tag value.
Use `*substring*` for a property/tag substring match.

## Properties

Property names and string values are case-insensitive. These properties
are supported; arbitrary metadata keys are not query-language properties.

| Property | Meaning | Examples |
| --- | --- | --- |
| `@name` | Editable trajectory name, stored as `id`. Matches the full name. An absent name does not match. | `@name:parallel-window-*`, `@name:"my trajectory"` |
| `@uuid` | Immutable trajectory UUID. Matches the full UUID. | `@uuid:12345678-*` |
| `@state` | Validation stage: unfinished (-3), rejected (-2), unsuccessful (-1), pending (0), candidate (1), validated (2), ready (3). Accepts the name or numeric level; missing/null levels count as unfinished, matching the dashboard. | `@state:candidate`, `@state:-2` |
| `@batch` | Generation batch ID from `metadata.generation_batch`. Missing/null IDs do not match, including `@batch:*`. | `@batch:12345678-*`, `-@batch:*` |
| `@generation` | Automated generation status, independent of validation: `unfinished` or `complete`. Excludes manual entries and known copies of another sample's generation checkpoint. | `@generation:unfinished`, `@generation:complete @state:pending` |
| `@windows` | Number of distinct declared windows, including implicit visible tool-call windows. Repeated opens/calls in one window count once; invisible calls add no window. | `@windows:>5`, `@windows:<=3`, `@windows:2` |
| `@uses_tool` | A tool called by an authored `tool-call` or `invisible-tool-call` event. Merely advertising a tool with `show_tools` does not count. | `@uses_tool:calculator`, `@uses_tool:file_*` |
| `@errors` | Whether local compilation fails with a compiler error. | `@errors:true`, `@errors:false` |
| `@warnings` | Whether local compilation succeeds and reports at least one compiler warning. | `@warnings:true`, `@warnings:false` |

Window counts accept `=`, `!=`, `<`, `<=`, `>`, and `>=`, followed immediately
by a nonnegative integer. Omitting the operator means equality. Values must fit
a signed 64-bit integer. State names/levels require an exact match; unknown states
report an error. Errors and warnings accept only `true` or `false`,
case-insensitively; other values and wildcards report an error. Name, UUID, batch
and tool properties accept literal values and `*` globs.
Generation accepts exactly `unfinished` or `complete`, case-insensitively; it does
not accept wildcards or validation-stage names.
Negation and grouping work with properties: `-@uses_tool:subagent` excludes
trajectories using that tool; `-(@windows:>5 @uses_tool:calculator)` excludes
only trajectories satisfying both conditions.

Use `@generation:unfinished` to find the same saved automated drafts targeted by
**Resume Unfinished Generations**: they have generation phase `planning`, an
automated origin and a nonempty generation-job ID. Paused, failed, cancelled or
interrupted drafts remain in this group until generation completes. Missing or
corrupt checkpoints remain visible for recovery; copies known to belong to
another trajectory's checkpoint are excluded. A match does not promise that
resumption will succeed without repair.

`@generation:complete @state:pending` finds generated samples awaiting review.
`@state:unfinished` is the distinct validation stage (-3); it does not identify
unfinished generation. Manual entries do not match either generation value.
Negation still includes other entries: `-@generation:unfinished` also includes
manual samples and legacy entries, not just completed generations. Combine with
a scenario tag or batch, for example `@generation:unfinished #user_scenario` or
`@generation:unfinished @batch:<batch-id>`.

Use `@errors:false @warnings:false` to find trajectories that compile without
diagnostics, or `@errors:true | @warnings:true` to find trajectories needing
attention. The compiler stops at its first error and does not return warnings
for that trajectory, so a failing trajectory matches `@warnings:false`.
`@errors:false` alone still includes trajectories with warnings. These filters
refer to compiler diagnostics, independently of the `@state` review stage.

Diagnostic searches compile saved trajectories locally on first use after opening
a project, using its tokenizer and configured tools. Compilation runs the local
sandbox simulation without contacting model providers. Results are cached by
source hash and refreshed after edits or tool changes; subsequent searches reuse
unchanged results. The initial diagnostic search can take longer on large projects.
Search does not change source, revisions, review state, or pinned tool dependencies.

For typed arbitrary metadata filters and grouped counts, use the separate MCP
`query_trajectories` and `get_metadata_counts` tools.

## Tags

`#coding` matches a complete tag named `coding`, case-insensitively. A stored
leading `#` is optional: both `coding` and `#coding` match. Only the trajectory's
`metadata.tags` array is searched; `#coding` appearing in replay text is not a tag.

`#*math*` matches any tag containing `math`; `-#*math*` excludes trajectories
with any such tag. Trajectories with no tags satisfy `-#*math*`. `#code` does not
match the tag `coding`. Combine tags with spaces for AND, or `|` for OR:
`#coding #reviewed`, `(#coding | #math) python`.

## MCP examples and pagination

Call `get_authoring_guide(topic="search")` to read this guide from any MCP
client, with no repository access required. Then call:

```json
{"name":"list_trajectories","arguments":{"query":"python -(math complex) #coding","offset":0,"limit":50}}
```

```json
{"name":"list_trajectories","arguments":{"query":"@name:parallel-window-* @windows:>5 -#*math*","offset":0,"limit":50}}
```

Results include the total match count, current dataset revision, and paginated
trajectory attributes/metadata without replay payloads. All filtering occurs
before pagination; terms can match beyond the first page. Results keep dataset
order. Fetch an individual result with `get_trajectory(sample_id=...)`.
Use `offset` and `limit` for subsequent pages. The dashboard shows 100 per page;
MCP defaults to 50 and accepts limits from 1 through 5000.

To discover five candidates for subscription-backed Codex review, call:

```json
{"name":"list_trajectories","arguments":{"query":"@state:candidate","limit":5}}
```

Restrict a review to one batch with `@state:candidate @batch:<batch-id>`.
`limit` is a separate tool argument, not part of the query string. These searches
do not contact model endpoints. The reviewer reads and assesses the samples itself;
`validate_trajectory(semantic=false)` provides compiler diagnostics without API calls.

## Invalid queries and storage

Unclosed parentheses/quotes, empty groups, missing operands (`python |` or `-`),
empty tags, unknown properties and invalid property values report an error.
Queries are limited to 4096 characters, 256 tokens and 32 levels of nested
groups/negations. The dashboard displays errors below the search box; MCP
returns a tool error. Fix the query and search again.

Search uses cached text, tool usage, metadata tags and window counts in SQLite.
Edits, imports, duplication, deletion, undo and redo update these projections
transactionally. Existing schema 3/4 projects upgrade automatically to schema 5
on first open, reading one trajectory at a time to build the new search data;
this preserves source JSON, content hashes and dataset revisions. Subsequent
opens and searches do not decode every trajectory in Python.
