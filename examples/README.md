# Examples

Start with the first two. Both are importable through **Project → Import trajectories…** in the studio.

| File | What it is |
| --- | --- |
| [`graph_minimal.json`](graph_minimal.json) | A small graph source. The best first import. |
| [`graph_tools.json`](graph_tools.json) | A tool call split into fragments, with its response. See [Synthetic tools and chat](../docs/TOOLS.md). |
| [`graph_local_llm.json`](graph_local_llm.json) | A template for endpoints and roles. Fill in your own URLs and models, and keep credentials in the server environment. |
| [`ktremix_dataset.py`](ktremix_dataset.py) | A public reader, sampler and validator for [KTREMIX](../docs/KTREMIX.md) shards. |
| [`ktremix_training_data.py`](ktremix_training_data.py), [`train_ktremix.py`](train_ktremix.py) | The letter-addition training demo, which uses the real compiler. |

Before running anything that starts model work, read [Local-model validation](../docs/GRAPH-LOCAL-LLM.md),
the [training demo](../docs/KTREMIX-TRAINING-DEMO.md) and [Acceptance](../docs/ACCEPTANCE.md). These scripts don't run during
ordinary test collection.
