# Tools and chat

Tool definitions in Project Settings supply `name`, `input_schema` and optional
`response_window`; they have no executable implementation or output schema.
`tool_call` events name `tool` and `call_id`. For fragments independently choose
`insert_tool_begin` / `insert_tool_end`. End submits the assembled request;
closing before end abandons the prefix. Keep partial argument text as a literal
string. Form mode requires complete object JSON; switching modes preserves raw
content, including intentionally incomplete JSON and unknown argument keys.

`tool_response` uses the matching `call_id`, depends on a submitted ancestor,
and has independent `insert_response_begin` / `insert_response_end` flags.
Responses are free-form. External `delivery.mode` is `instant` or `streamed`;
`delivery.chain_immediately` defaults true. `replace_body` changes the visible
body atomically; it does not open another logical window.

`chat_message` targets a `purpose: "chatroom"` window and includes
`chat: {"speaker":"User"}`. MSG framing is automatic and atomic. Optional
`call_id` links an authored tool echo. Tool calls may disable `echo_chat`.
Whole-event `supervision` controls `masked` and `weight`, including framing;
external events default masked. Never manually insert special token strings.
