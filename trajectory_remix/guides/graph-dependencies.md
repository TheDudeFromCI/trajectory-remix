# Dependencies and windows

An event names one logical `window` and an `after` list of predecessor event IDs.
Set the whole `after` list with `update_event`.
Roots use `[]`. Same-window writers must be ancestor ordered. References to
missing events/windows remain diagnostic errors. Deleting an event preserves
remaining dependencies; remove broken strings explicitly. Never infer time from
map order, use preview coordinates as authored delays, or silently reconnect.

Create/update windows with `patch_graph`: a window has `purpose`,
`agent_writable`, and optional configured `reasoning_effort`. Use stable logical
IDs, independent of reusable physical tokenizer slots. `close_window: true`
derives EOS. A closed logical ID cannot reopen. Typed content references use
`{"kind":"window_ref","window":"document"}` alongside text pieces; plain
control-looking strings remain literal.

Graph mode scrubs dependency order without tokenization. Opt-in Tick history
uses a client-selected tokenizer and shows actual scheduler frames read-only.

Use `configure_window` to prepare a window before its first event. The authoring tools
prune a used window when its final event or content reference is removed.
