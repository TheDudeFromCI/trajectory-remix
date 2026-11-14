# Examples

| Path | What it is |
| --- | --- |
| `demo-tokenizer.json` | A tiny ASCII character tokenizer for trying Remix. Use your real tokenizer for real datasets. |
| `reactive-sample.json` | A small reactive trajectory with a tool. Import via **File → Import**. |
| `build-graph-analysis.json` | A complete worked example: virtual Python modules, concurrent file tools, separate reasoning and chat windows. Timed for another tokenizer, so retime it with the demo one. Also available from MCP via `get_authoring_guide(topic="example")`. |
| `parallel-tools.json`, `persistent-agents.json`, `samples.jsonl` | More sample trajectories to import. |
| `create_demo.py` | Regenerates the demo tokenizer and fixtures. |
| [`custom_tools/`](custom_tools/README.md) | Example custom Python tools: one visible, one invisible-only. |

A first run with the demo tokenizer:

```bash
python -m trajectory_remix init \
  --tokenizer examples/demo-tokenizer.json --dataset demo.sqlite
python -m trajectory_remix --dataset demo.sqlite
```

Then import a sample from the dashboard. See [Getting started](../docs/GETTING-STARTED.md).

Generation scenarios (`user_scenario`, `idle_scenario` and the Kidra flows) are built in
and need no example files. To write your own, see
[Custom scenarios](../docs/SCENARIOS.md#custom-scenarios).
