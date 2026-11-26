# Synthetic tools and chat

Trajectories often show an agent calling tools: reading a file, searching, posting a chat
message. In Remix those tools are **synthetic**. A tool is only a JSON input schema, and the
call and its result are both events someone writes. Nothing is ever executed, so results are
exactly what the author or the generating model wrote.

## What a tool is

- A catalogue entry in [Project settings](PROJECT-SETTINGS.md): an ID, a name, an
  **input schema**, and optionally a `response_window` for its results.
- No code and no output schema. Results are free-form text.
- Arguments are checked against the input schema when a call is **submitted**. A mismatch is a
  warning, not an error, because training data may deliberately include bad calls.

Fresh projects include a `chat` tool whose input has a string `content`. It's how the agent
talks to people in generated episodes. You can remove it, but then episodes have no chat replies.

## Writing a call

A complete call is a `tool_call` event, usually followed by a `tool_response` that depends on
it:

1. `tool_call` in an agent-writable window, with a `call_id`, the `tool` ID and the arguments
   as content.
2. `tool_response` with the same `call_id`, placed in the tool's response window.

[`graph_tools.json`](../examples/graph_tools.json) is a worked example. The details are in
[Source format](GRAPH-SOURCE.md#tool-calls-and-fragments), and the short agent lesson is
[`graph-tools-chat.md`](../trajectory_remix/guides/graph-tools-chat.md).

### Show the agent its tools

Before an agent calls a tool, a trajectory can show it which tools exist. In the Editor,
add an event, set its **Kind** to **Tool schema** and put it in a window whose purpose is
`tools`:

1. Tick the tools to list on the **Content** tab. The text box is replaced by one checkbox
   per project tool. Each tick writes that tool's description and parameters to the
   event.
2. Tools you don't tick can still be added at random when the trajectory is compiled, so
   the agent also learns to ignore tools it doesn't need. The event stores only the tools
   you ticked, and you can tick any tool as usual. The preview shows the random ones grayed
   out. Set the chance in **Settings → Project defaults** (0 turns it off), or hide them in
   **Preferences**.
3. Make the first events of the trajectory follow the listing, so the agent sees it.

A `tool_schema` event is masked external input with one JSON line per tool (name,
description, parameters) for the chosen tools only. It is what Assembly writes during
generation. The compiler (`graph/tool_extras.py`) adds the random extras, and they repeat:
the same trajectory ID and event ID always add the same tools. The old `extra_tools` field
is ignored.

### Partial and abandoned calls

Calls can be written in **fragments** (begin-only, middle, end-only) so the agent's request
spans other events. This is how you represent a request that streams while something else
happens. The assembled request is submitted at tool end. If the window closes first, the
partial request is **abandoned** and needn't be valid JSON, which is useful for modelling
interrupted calls.

### Results

A response chooses its own framing and delivery, independent of the call: begin/end markers
can each be on or off, delivery is instant or streamed, and the window can close afterward.
Failures are just results. Write a rate-limit error or a missing-file message as the response.

## Chat

Chat lives in a window with purpose `chatroom`. A `chat_message` has a `speaker` and is always
delivered all at once. The compiler derives message framing, so don't type delimiters.

With the `chat` tool, a successful call can **echo** into the room automatically: a masked
`chat_message` linked to the call by `call_id`. Turn the echo off with `echo_chat: false`
when the story needs something else, such as a filtered message or a rejection with no
delivery. Then write those outcomes yourself as events.

The Draft and Assembly models follow the same rules. See
[Event generation](GRAPH-GENERATION.md#tools-and-chat-during-generation).

## What was removed

The old executable sandbox, invisible tool calls, simulated clock and tick anchors no longer
exist. If an old trajectory depended on them, re-author the results as ordinary events.
