# Sampler Matrix workbench

The **Sampler Matrix** (the **Sampler** tab) is where you tune *what kinds of trajectories get generated*: how likely each
domain, lifecycle stage, user trait or behavior is, and which combinations are allowed. This page is the
workflow guide. The sampler itself and the linked metrics are described in
[Sampler and metrics](GRAPH-SAMPLER-METRICS.md), and generation in
[Event generation](GRAPH-GENERATION.md).

![Choice comparison and constraint inspector](images/sampler-workbench/desktop.png)

## Tune the matrix

1. **Open the Sampler tab.** Search names, descriptions or serialized constraints, and filter navigation to
   Dimensions, Behaviors, Decisions, Rules or Metrics.
2. **Pick a group** and compare its choices: numeric weights and base shares line up in a table you can edit
   with the keyboard. To change several at once, tick the choices and use **Set selected**. It affects only
   checked entries in that group, even ones a filter is hiding.
3. **Pin a choice.** The **Pin** column has a small button per supported choice. Muted is unpinned, and a
   filled, highlighted pin is selected. Click again to unpin, and picking a sibling moves the pin. Pins stay
   in sync with the preview dropdowns and survive navigation. They apply to previews and generation requests
   **without changing weights or saving anything**. A dash means the sampler can't pin that field.
4. **Inspect a choice.** Click it to see its constraints and the rules that reference it. Expand its linked
   metric to edit the label, units, target description and absolute and relative **tolerance bands**. Both
   bands go together, and clearing both leaves tolerance unspecified.
5. **Add or remove entries and rules.** *Add or remove entries* copies a choice to a new ID or removes it
   after confirmation. Copying a family prefixes its child behavior IDs. *Add or remove rules* offers simple
   condition, value and target controls for a single-condition multiplier or forced dimension.
6. **Use Advanced JSON** for anything else, such as multi-condition rules or custom fields. **Apply JSON to
   project draft** replaces only the current group and doesn't save. Apply it before leaving the group.
7. **Save project changes.** The whole matrix and metric customizations are saved with a settings revision
   check. Dangling references block saving and keep your draft, and pins and references are never silently
   rewritten. After a revision conflict, copy the group JSON, reload the latest settings and reapply.

## Preview a proposal

Expand **Proposal preview** **after saving**, because the preview samples the *saved* matrix, not the browser
draft. Set a fixed integer seed and optional pins, then sample one proposal. You'll see its payload and matrix
digests and the settings revision it used. The preview creates an immutable proposal snapshot and doesn't start
generation, and existing runs keep their frozen inputs.

When you're happy, generate from here ([Event generation](GRAPH-GENERATION.md#run-a-generation)).

![Shared rule inspection and advanced editing](images/sampler-workbench/rules.png)

## Reading the numbers

- **Base share** is just `weight ÷ group total`. Conditional multipliers, eligibility, forced values and joint
  balancing are applied by the original sampler, so base shares are **not** measured output frequencies or
  exact joint probabilities. Event tags show their unnormalized base probabilities and explain caps.
- Weight edits only update the draft, the share cells and navigation summaries. The detail pane doesn't rebuild,
  so your focus and selection stay put. Drafts live in this browser tab, with no automatic conflict merge or
  global project Undo.
- Unknown actual metrics stay unavailable in **Dataset Metrics**, and are never shown as zero.

## What it doesn't do

The workbench keeps things simple on purpose:

- Repeated edits are scoped to checked choices in one group. There is no cross-group bulk rewrite or
  spreadsheet paste.
- Nested constraints use JSON with shared backend validation rather than a second visual rule language. Editing
  JSON replaces the group, so omitted fields are removed deliberately.
- New top-level dimension groups, decision axes and arbitrary nested eligibility rules are advanced JSON work.
  There is no automatic reference repair.
- Narrow screens stack navigation above details and scroll the dense table sideways. Desktop is the primary
  surface.

## Checks

```bash
python -m pytest -q tests/test_graph_sampling.py
python tests/run_graph_frontend.py
python tests/run_sampler_frontend.py
```

The browser check needs the optional Node `playwright` package and a Chromium (`CHROMIUM_PATH`, default
`/usr/bin/chromium`). It uses a temporary project and a handful of seeded proposals, covering navigation,
keyboard focus, repeated edits, pins, previews, persistence, revision conflicts, frozen run snapshots and the
narrow layout, and refreshes the screenshots above. It runs no benchmarks, training, live model calls or
tokenizer work.
