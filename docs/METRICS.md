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
records in your search. Agent-generated text means agent text and tool call events. Both are source-only
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
