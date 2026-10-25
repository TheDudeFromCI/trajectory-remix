Plan ONLY tool calls needed for this exchange using the supplied public tool schemas. Return ONE JSON OBJECT with the key calls. The value is a list of batches; each batch is a list of call objects. Every call object has tool (string) and arguments (object).

Correct empty plan: {"calls": []}
Correct independent reads: {"calls": [[{"tool": "file_read", "arguments": {"path": "notes.md"}}, {"tool": "file_read", "arguments": {"path": "data.csv"}}]]}
Correct dependent batches: {"calls": [[{"tool": "file_write", "arguments": {"path": "a.txt", "content": "hello"}}], [{"tool": "file_read", "arguments": {"path": "a.txt"}}]]}

Never return a bare array, flat calls list, tool-name strings, [tool, args] pairs, or bracketed key/value pairs. Independent calls share a batch. Tools run in a virtual environment initialized from the scenario's authored files. Only subagent may have an authored outcome. Do not fabricate outcomes for other tools or send the final chat response using the chat tool. If retry_feedback is present, correct the specific formatting error.
