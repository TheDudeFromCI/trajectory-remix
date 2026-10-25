# Custom tool examples

This directory demonstrates the [custom Python tool interface](../../docs/SANDBOX.md#custom-python-tools) with a visible `spell` tool and an invisible-only `potato_mode` tool.

## Run

From the repository root, start Trajectory Remix with this directory as the custom tool folder:

```bash
python -m trajectory_remix --dataset project.sqlite --tools-dir ./examples/custom_tools
```

Each directly contained `.py` file is registered as a custom tool. The `spell` tool is then available for visible tool calls and `show_tools` events.

## Visible tool: spell

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

## Spell interface

`spell.py` defines the required module-level `name`, `description`, and JSON `schema`, plus synchronous `run(args, state, windows)`. Its return value is `(output, is_done, modified_state)`:

- `output`: JSON text containing the uppercase characters.
- `is_done=True`: complete the call in its first execution tick.
- `modified_state=False`: no sandbox state or window changes.

The input is required to be a string, and unexpected arguments are rejected by the schema.

## Invisible tool: potato_mode

`potato_mode.py` demonstrates a tool that runs only as an author-scheduled [invisible tool call](../../docs/SANDBOX.md#invisible-arguments-and-external-activity), rather than being advertised to the model.

The module declares `invisible = True` and has a zero-argument schema. For example, schedule this event when the `system` window is open:

```json
{"op": "invisible-tool-call", "tool": "potato_mode", "arguments": {}}
```

The tool finds the window named `system` and **replaces its content** with:

```text
You are a helpful potato assistant.
```

It returns `("", True, False)`: no tool observation, completion after one tick, and no sandbox state mutation. The `False` flag is correct because window changes are tracked independently from sandbox state. The tool requires an open `system` window and raises an error if none exists.

Since `invisible = True`, `potato_mode` is omitted from public tool listings and cannot be invoked by a visible model tool call or selected for `show_tools`. It can still be scheduled by a trajectory author using `invisible-tool-call`.
