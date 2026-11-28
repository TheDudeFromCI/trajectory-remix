# Metrics

The **Metrics** tab shows how your dataset compares with what the sampler intended to produce, without
guessing at anything it can't measure.

## What you see

- **Proposal coverage** summarizes the sampler's *targets* across your saved trajectories. These are
  intentions, not proof of behavior.
- **Derived labels**, such as `strategic_investment` (proposals weighing an automation or
  infrastructure choice, in any family), are counted from the same targets. They are available
  through the API and are not shown on the Metrics tab yet. See
  [Derived labels](GRAPH-SAMPLER-METRICS.md#derived-labels).
- **Observations** are saved measurements of finished sources, each with its origin and the revision it was
  made at. Out-of-date ones are flagged **stale**.
- **Unavailable** values stay unavailable. A missing measurement is never shown as zero.

Filter the population with the same search you use in the sidebar. Unsaved drafts are excluded.

## Set a trajectory's metrics

Categories, labels and outcomes can't be calculated, so you set them by hand in the Editor:

1. Select a trajectory.
2. Open the **Metrics** tab in the event panel (after **JSON**).
3. At the top, under **Trajectory**, set **Stage**, **Split** and **Complexity**, and read the trajectory's
   **Size**. A missing split or stale values show up as warnings on the **Problems** tab.
4. Below that, pick a value, tick labels or type an outcome. Each change saves at once.

| Group | What it holds | How you set it |
| --- | --- | --- |
| **Categories** | One choice per sampler dimension, such as `complexity`, `task_length` or `domain` | A dropdown |
| **Labels** | Event tags and secondary competencies or behaviors | Checkboxes (click the summary to open them) |
| **Outcomes and notes** | Free values such as `actual_outcome` or `accepted` | Text, a number, `true`/`false` or JSON |
| **Measured** | Values calculated from the source or an export | Read-only; use **Calculate missing metrics** |

**Clear** removes a value so it reads as unavailable again. A **Stale** value was recorded against an older
source or definition; **Confirm** records it again for the current source. Tick **Only show unset** to
list just the values still missing. Each change is one **Undo** step. The tab is read-only while a job
holds the trajectory.

**Sample unset from matrix** fills every unset category and label in one step. It draws one sample from
the [Sampler matrix](SAMPLER-WORKBENCH.md) that fits the values you already set, and keeps those values.
Filled values are recorded with origin `matrix_sample`. It is one **Undo** step, like any other change.

**Complexity** sits in the **Trajectory** section, next to Stage and Split, rather than in the category
list. When it's unset, the picker shows the sampler's target as a hint.

A value you set is recorded against the trajectory's current content. If the content changes afterwards,
the value is marked **Stale** until you **Confirm** it again, and the **Problems** tab lists it under
`stale_metrics`.

You can also ask the Assistant to do it: leave a comment such as "complexity should be hard" and
[send the comments to the agent](DASHBOARD.md#send-comments-to-an-agent). It can read every metric and set
categories, labels and outcomes, but not measured values. Its values arrive with its other
[proposed changes](DASHBOARD.md#review-the-assistants-proposed-changes) and are saved when you apply them, with the origin
`assistant_annotation` (visible through the API), and you can change or clear them here like any other value.

## Calculate what's missing

**Calculate Missing Metrics** starts an explicit bulk job. It fills only values that are absent, using
calculators that read the source alone. It **never tokenizes, compiles or calls a model**, so it's cheap and
safe to run. Stale or manual values are left for you to edit with a revision check.

Token-exposure and peak-capacity measurements need an explicit compiled export with a profile. They aren't
published back to the source automatically.

## Where to read more

- [Sampler and metrics](GRAPH-SAMPLER-METRICS.md): provenance, tolerances and drift, coverage, REST routes and
  which fields are unavailable.
- [Sampler Matrix workbench](SAMPLER-WORKBENCH.md): editing the linked target and metric definitions.

## Character counts and trajectory sizes

The Metrics tab also shows **Total characters** and **Agent-generated characters** for the saved
records in your search, each followed by an estimated token count such as `(~1,000 tokens)`
(characters / 3.5). Agent-generated text means agent text and tool call events. Both are source-only
calculators, so **Calculate Missing Metrics** can save them per trajectory.

Each trajectory also gets a **size label**. Tokens are estimated at about 3.5 characters per token (nothing is
tokenized), and the label whose *total tokens* is nearest in log2 distance wins. Empty trajectories get the
smallest label. The Metrics tab lists how many trajectories fall in each size.

You can edit the labels under **Project settings → Project defaults → Trajectory size labels** (also the
`size_labels` setting). The defaults:

| Name | Agent tokens | Total tokens |
| --- | --- | --- |
| XXXS | 0.25k | 4k |
| XXS | 0.5k | 8k |
| XS | 1k | 16k |
| S | 2k | 32k |
| M | 4k | 64k |
| L | 8k | 128k |
| XL | 16k | 256k |
| XXL | 32k | 512k |
| XXXL | 64k | 1024k |

Here `k` is 1024 tokens. Label names must be unique.

### Searching and showing size

Type `@size:` plus a label in the Editor search box to filter by size, alone or beside other text:

| Search | Matches |
| --- | --- |
| `@size:m` | Exactly M |
| `@size:<=xs` | XS or smaller |
| `@size:>l` | Larger than L |
| `retry @size:>=xl` | Text "retry" among XL or larger trajectories |

Use `<`, `<=`, `>`, `>=` or `=` (the default). Names ignore case and follow your configured labels, ordered by
their total tokens. The same filter applies to Metrics, bulk edits and exports. Each trajectory's size also
appears as a **Size** tag in the collapsible trajectory header (from its last saved source).
