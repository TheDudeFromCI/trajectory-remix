# Metrics

The **Metrics** tab shows how your dataset compares with what the sampler intended to produce, without
guessing at anything it can't measure.

## What you see

- **Proposal coverage** summarizes the sampler's *targets* across your saved trajectories. These are
  intentions, not proof of behavior.
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
