# Replay format

This page explains how a trajectory's source JSON is interpreted. It follows
**Project Kidra — Multi-Window Trajectory Replay Specification v1.0**
(20 October 2026), as implemented by the compiler. The current
repository is not audited against it.

## A first example

```json
{
  "schema-version": "1.0",
  "id": "warning-example",
  "tick-policy": "external-first",
  "defaults": {"implicit-noop-loss-weight": 0.1},
  "replay": {
    "0": [
      {"op": "open", "window": "tool", "window-type": "tool"},
      {"op": "open", "window": "reasoning", "window-type": "reasoning"}
    ],
    "1": [
      {"op": "append", "window": "tool", "source": "tool", "delivery": "stream",
       "content": "Start. WARNING!{N_TICK:4} Retrying... Done.{N_TICK:5}",
       "loss": {"mask": "exclude"}}
    ],
    "{N_TICK:4}+1": [
      {"op": "generate", "window": "reasoning", "content": "A warning arrived.<|eos|>"}
    ],
    "{N_TICK:5}+1": [
      {"op": "generate", "window": "tool", "content": "<|eos|>"}
    ]
  }
}
```

`replay` maps a **tick** (a number, or an anchor expression) to a list of events.
At tick 0 two windows open. At tick 1 text streams into the `tool` window from an
external source, one token per tick. When the word "WARNING!" has been delivered
(anchor `N_TICK:4`), the model reacts one tick later in the `reasoning` window.

## Events

| `op` | Meaning |
| --- | --- |
| `open` | Open a window. Optional `window-type` (default `document`). |
| `append` | External text added to a window. `delivery` is `instant` (default) or `stream`; streams default to one token per tick, adjustable with `tokens-per-tick`. |
| `generate` | The model writes text into a window, one token per tick. |
| `tool-call` | A visible tool request and its result window. See [Tools](TOOLS.md). |
| `invisible-tool-call` | Author-only world activity the model does not see. |
| `show_tools` | Advertise tool schemas in an open window. |

A window is **closed only by a generated EOS token**, which you include in a
`generate` event. An explicit `close` is rejected. The compiler never closes a window
for you, and window IDs cannot be reused. Open windows at the end of the replay are
allowed and reported.

## How each tick runs

Every tick has four phases:

- **A. Open.** Windows open and each receives exactly one BOS token followed by an
  automatic header such as `<|ctx|>Window: 4\nPurpose: tool<|/ctx|>`. Both are
  conditioning context with no loss. Opening uses no extra ticks.
- **B. External input.** Appends are applied, instantly or streamed.
- **C. Model decisions.** Every window's next token is chosen from one shared snapshot
  taken after phase B.
- **D. Commit.** Generated tokens are committed, windows that emitted EOS close, and
  their KV memory is released.

Consequences:

- Windows cannot see each other's tokens generated in the same tick.
- Generation emits one token per window per tick, and independent windows can
  generate simultaneously.
- An idle open window emits an implicit **NOOP** target at the configured loss weight
  (`defaults.implicit-noop-loss-weight`). NOOPs are never stored as window content.
- External content is excluded from loss by default but still conditions the model.

## Anchors and tick expressions

An **anchor** `{N_TICK:n}` is a zero-width marker you place anywhere in a content
stream. It captures the tick at which the preceding text finishes committing. Use it
as a replay key to schedule something relative to that moment:

- `{N_TICK:4}+1` means "one tick after anchor 4 was captured".
- `max({N_TICK:7}+1, {N_TICK:8}+1)` joins two branches, and maxima may be nested.

Rules:

- Anchors may not be duplicated, undefined, cyclic or malformed. An anchor may not
  split a tokenizer token; text is tokenized once as a whole, not per fragment.
- `{N_TICKS}` is not valid. Use `\{N_TICK:4}` for literal marker text.
- At an empty prefix an anchor captures the event's start. All anchors on an
  instant append capture that append's start tick.
- A phase-D capture cannot trigger an event in the same tick. A phase-B capture can
  trigger generation in another window the same tick, but opening or appending from
  it needs `+1`. Use `+1` for portable reaction schedules.

Events at the same resolved tick run in key order (numeric keys first, then symbolic,
sorted), and list order within a key is kept. Overlapping writers to one window are
rejected, except several instant external appends, which concatenate in list order.
An `open` must precede content at the same tick.

## Loss

By default external content has no loss and generated content does. Override with:

- `loss.mask`: `include`, `exclude` or `auto`.
- `loss.weight`: a finite, nonnegative number.
- `segments: [{text, loss}, ...]` instead of `content`, for per-segment loss. Each
  segment boundary must fall on a token boundary.

Generated EOS has normal target loss.

## Referring to windows

Write `{WINDOW:tool0}` in `content` or segment `text` to refer to a window by its
logical ID. At compile and export time it is replaced by that window's numeric slot
(the same number shown in its header). The source keeps the marker.
`\{WINDOW:tool0}` is literal text. A reference to an undeclared window is an error,
but later declarations and implicit tool windows are fine. Tool arguments and real
sandbox observations are never rewritten.

## Window slots and headers

Each window gets a numeric slot from the project's allocation policy `uuid-hash-v1`:
a SHA-256 stream seeded by the trajectory's immutable UUID. Slots are unique, are
never reused, and do not depend on label edits or randomness. Exceeding the project's
`max_window_slots` rejects creation, import, edits, generation and export.

With an unlimited budget the slot space equals the number of declared windows, so
adding windows can change numbers. Prefer `{WINDOW:id}` markers in source.

Headers appear in replay, generation context and exports. Set
`defaults.window-headers` to `false` to omit them (default `true`).

## Limits

A compiled trajectory may have at most 100,000 ticks and 500,000 expanded rows. Rows
are token-level records, including implicit NOOPs, so the row count is not a measure
of text size. See [Project storage and limits](STORAGE.md#limits) for preview limits.

## Related

- [Tools](TOOLS.md) and [Sandbox](SANDBOX.md) for tool calls and their timing.
- [Export](EXPORT.md) for how replay rows become training records.
