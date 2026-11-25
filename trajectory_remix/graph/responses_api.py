"""Translate graph job messages to and from the OpenAI Responses API."""

from . import token_limits

# The job owns conversation history, tool schemas, and the response lifecycle.
RESERVED_BODY_FIELDS = frozenset(
    {
        "input",
        "max_output_tokens",
        "reasoning",
        "tools",
        "tool_choice",
        "store",
        "include",
        "background",
        "previous_response_id",
        "conversation",
        "truncation",
    }
)
REPLAY_ITEMS = "responses_output"


def input_items(messages):
    """Replay completed Responses items, or translate turns kept by Chat Completions."""
    items = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            items.append(
                dict(
                    type="function_call_output",
                    call_id=message["tool_call_id"],
                    output=message["content"],
                )
            )
        elif role == "assistant" and REPLAY_ITEMS in message:
            items.extend(message[REPLAY_ITEMS])
        else:
            if message.get("content") is not None:
                items.append(dict(role=role, content=message["content"]))
            for call in message.get("tool_calls", []):
                items.append(dict(type="function_call", call_id=call["id"], **call["function"]))
    return items


def request_context(payload):
    context = dict(input=input_items(payload["messages"]))
    if "tools" in payload:
        context.update(
            tools=[
                dict(
                    tool["function"], type="function", strict=tool["function"].get("strict", False)
                )
                for tool in payload["tools"]
            ],
            tool_choice="auto",
        )
    return context


def request_body(endpoint, context):
    # Keep retries independent of server state. Tool exchanges replay encrypted
    # reasoning with the completed output items in their original order.
    body = dict(endpoint.get("extra_body", {}))
    body.update(context, model=endpoint["model"], stream=False, store=False)
    if "tools" in context:
        body["include"] = ["reasoning.encrypted_content"]
    output_limit = endpoint.get("max_output_tokens", token_limits.DISABLED)
    if output_limit != token_limits.DISABLED:
        body["max_output_tokens"] = output_limit
    if endpoint.get("reasoning") is not None:
        body["reasoning"] = {"effort": endpoint["reasoning"]}
    for key in ("temperature", "top_p"):
        if endpoint.get(key) is not None:
            body[key] = endpoint[key]
    return body


def result(data, native):
    """Keep billed usage even if the response was incomplete or unusable."""
    if not isinstance(data, dict):
        raise ValueError("Expected a Responses API response object")
    meta = dict(model=data.get("model"), usage=data.get("usage"))
    status = data.get("status")
    if status != "completed":
        details = data.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        finish = "length" if reason == "max_output_tokens" else reason or status
        return dict(meta, invalid_output=True, finish_reason=finish)

    output = data.get("output")
    if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
        return dict(meta, invalid_output=True, finish_reason="stop")
    text, calls, call_ids = [], [], set()
    for item in output:
        kind = item.get("type")
        if kind == "message":
            parts = item.get("content")
            if not isinstance(parts, list) or item.get("status") not in (None, "completed"):
                return dict(meta, invalid_output=True, finish_reason="stop")
            text.extend(
                part["text"]
                for part in parts
                if isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            )
        elif kind == "function_call" and native:
            call_id, name, arguments = (item.get(key) for key in ("call_id", "name", "arguments"))
            if (
                not all(isinstance(value, str) and value for value in (call_id, name))
                or not isinstance(arguments, str)
                or call_id in call_ids
                or item.get("status") not in (None, "completed")
            ):
                return dict(meta, invalid_output=True, finish_reason="tool_calls")
            call_ids.add(call_id)
            calls.append(
                dict(id=call_id, type="function", function=dict(name=name, arguments=arguments))
            )
    if native and (text or calls):
        message = dict(role="assistant", content="".join(text) if text else None)
        if calls:
            message["tool_calls"] = calls
        message[REPLAY_ITEMS] = output
        return dict(meta, message=message)
    if text:
        return dict(meta, text="".join(text))
    return dict(meta, invalid_output=True, finish_reason="stop")
