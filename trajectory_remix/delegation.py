"""Tool-capable configured LLM workers, bounded independently of the coordinator."""

import json

from .agents import CHILD_TOOLS
from .dataset import reject_constant
from .tools import SCHEMAS, ToolError


async def run_worker(client, endpoint, history, call_tool):
    messages = [
        {
            "role": "system",
            "content": (
                "Complete the delegated task in this persistent conversation. Your private workspace "
                "contains only explicitly shared artifact versions and your previous work. Use file tools "
                "to write deliverables; they are published to the shared workspace when you finish. "
                "Run tests using python_run when available. Distinguish executed evidence from assumptions. "
                "Return a concise response referring to artifacts and evidence rather than copying files."
            ),
        },
        *history,
    ]
    schemas = [s for s in SCHEMAS if s["function"]["name"] in CHILD_TOOLS]
    calls_used = 0
    for _ in range(32):
        try:
            message = await client.complete(endpoint, messages, schemas)
        except Exception as error:
            raise ToolError("Configured subagent request failed") from error
        if not isinstance(message, dict):
            raise ToolError("Malformed worker response")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or calls_used + len(calls) > 64:
            raise ToolError("Worker tool call limit exceeded")
        if not calls:
            text = message.get("content")
            if not isinstance(text, str) or not text.strip() or len(text) > 200_000:
                raise ToolError("Worker must return bounded nonempty text")
            return text
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": calls,
            }
        )
        for call in calls:
            if (
                not isinstance(call, dict)
                or not isinstance(call.get("id"), str)
                or not isinstance(call.get("function"), dict)
            ):
                raise ToolError("Malformed worker tool call")
            function = call["function"]
            try:
                arguments = json.loads(
                    function.get("arguments", "{}"), parse_constant=reject_constant
                )
            except (ValueError, TypeError) as error:
                raise ToolError("Worker tool arguments must be JSON") from error
            outcome = await call_tool(function.get("name"), arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(outcome["outcome"], ensure_ascii=False),
                }
            )
            calls_used += 1
    raise ToolError("Worker round limit exceeded")
