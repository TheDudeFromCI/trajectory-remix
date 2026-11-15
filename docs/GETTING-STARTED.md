# Getting started

Trajectory Remix authors dependency graphs and compiles them into training data
on export. You need Python 3.11+; Node is only needed for frontend development
checks. There is no frontend build step.

```bash
python -m pip install -e '.[mcp,export]'
python -m trajectory_remix graph-init --dataset project.sqlite
python -m trajectory_remix graph-studio --dataset project.sqlite
```

Open `http://127.0.0.1:8765`. Import
[graph_minimal.json](../examples/graph_minimal.json) through File → Import, or
create a detached trajectory from the dataset sidebar. Add logical windows and
events in the right inspector. Use dependencies to order events; graph authoring
needs no tokenizer. Structural errors remain editable and block compiled export.

[Dashboard](DASHBOARD.md) covers endpoint forms, generation settings, job controls
and live updates. [Project settings](PROJECT-SETTINGS.md) covers model roles,
authentication, tokenizer profiles and review criteria. [Source format](GRAPH-SOURCE.md)
explains intrinsic data versus optional workflow metadata.

Configure a compatible tokenizer profile only for Tick history or compiled
export. Graph preview and export select their own profiles. Special tokens must
already exist in the tokenizer; Remix does not add or renumber them.

Generated trajectories pass through the configured review stages. Ready is a
manual decision. Tools and their outputs are synthetic authored events; no
sandbox or tool execution is available. Old replay projects are unsupported.
See [bounded tests](TESTING.md) and [acceptance](ACCEPTANCE.md) for verification.
