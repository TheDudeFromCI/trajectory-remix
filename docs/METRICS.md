# Metrics

The sampler matrix defines linked target/metric categories. Proposal targets are
not measured actuals. Dataset Metrics reports saved observations with origin and
revision; missing values stay unavailable. Calculate Missing Metrics is explicit,
uses source-only calculators and never token-compiles or calls a model.

See [sampler/metric contracts](GRAPH-SAMPLER-METRICS.md) for provenance, tolerances,
coverage, unavailable fields and export-only measurements; [Workbench B](SAMPLER-WORKBENCH.md)
for editing linked definitions. Token-exposure measurements need an explicit
compiled export/profile and are not automatically published back to source.
