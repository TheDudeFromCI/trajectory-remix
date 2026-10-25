# Custom tool example: spell

This directory demonstrates the [custom Python tool interface](../../docs/SANDBOX.md#custom-python-tools) using a stateless, single-tick tool named `spell`.

## Run

From the repository root, start Trajectory Remix with this directory as the custom tool folder:

```bash
python -m trajectory_remix --dataset project.sqlite --tools-dir ./examples/custom_tools
```

Each directly contained `.py` file is registered as a custom tool. The `spell` tool is then available for visible tool calls and `show_tools` events.

## Example

Tool name: `spell`

Arguments:

```json
{"text": "strawberry"}
```

Tool result (a JSON-encoded array of uppercase characters):

```json
["S", "T", "R", "A", "W", "B", "E", "R", "R", "Y"]
```

The tool also handles spaces, punctuation, and empty strings; an empty string produces `[]`.

## Interface

`spell.py` defines the required module-level `name`, `description`, and JSON `schema`, plus synchronous `run(args, state, windows)`. Its return value is `(output, is_done, modified_state)`:

- `output`: JSON text containing the uppercase characters.
- `is_done=True`: complete the call in its first execution tick.
- `modified_state=False`: no sandbox state or window changes.

The input is required to be a string, and unexpected arguments are rejected by the schema.
