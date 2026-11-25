"""Stateless OpenAI-compatible transport. Queue owns all retries and ordering."""

import asyncio
import json
import math
import os

import httpx

from . import responses_api, token_limits
from .failure_reason import key_variable


def context_budget(payload, messages):
    wire_contract = {"tools": payload["tools"], "tool_choice": "auto"} if "tools" in payload else {}
    return _context_budget(payload["endpoint_config"], dict(messages=messages, **wire_contract))


def _context_budget(endpoint, context):
    """Conservative byte-token ceiling, independent of training tokenizers.

    UTF-8 bytes bound byte-backed BPE input tokens; reserve additional template
    overhead. This deliberately rejects some requests that a model tokenizer
    could fit. Never truncate evidence to make a request appear to fit.
    """
    limit = endpoint.get("context_window_tokens", token_limits.DISABLED)
    output = endpoint.get("max_output_tokens", token_limits.DISABLED)
    ratio = endpoint.get("bytes_per_token", 3.0)
    size = len(json.dumps(context, ensure_ascii=False).encode())
    return dict(
        input_bytes=size,
        bytes_per_token=ratio,
        input_tokens_estimate=math.ceil(size / ratio),
        input_ceiling=token_limits.input_ceiling(limit, output),
        context_window_tokens=limit,
        max_output_tokens=output,
    )


def request_body(payload, trajectory_id):
    """The exact HTTP body for a request (no credentials) and its context budget."""
    endpoint = payload["endpoint_config"]
    if endpoint.get("use_responses_api"):
        context = responses_api.request_context(payload)
        return responses_api.request_body(endpoint, context), _context_budget(endpoint, context)
    native = "tools" in payload
    # Requests without tools (the Draft) ask for plain text: no response_format, so
    # servers without JSON mode or grammar support work unchanged.
    messages = [
        {key: value for key, value in message.items() if key != responses_api.REPLAY_ITEMS}
        for message in payload["messages"]
    ]
    budget = context_budget(payload, messages)
    extras = endpoint.get("extra_body", {})
    if native:
        extras = {
            k: v
            for k, v in extras.items()
            if k not in {"tools", "tool_choice", "parallel_tool_calls"}
        }
    body = dict(
        # Provider options first; validation stops them replacing the fields below.
        **extras,
        model=endpoint["model"],
        messages=messages,
        stream=False,
    )
    if budget["max_output_tokens"] != token_limits.DISABLED:
        body["max_tokens"] = budget["max_output_tokens"]
    if native:
        body.update(tools=payload["tools"], tool_choice="auto")
    for key in ("temperature", "top_p", "presence_penalty"):
        if endpoint.get(key) is not None:
            body[key] = endpoint[key]
    if endpoint.get("reasoning") is not None:
        body["reasoning_effort"] = endpoint["reasoning"]
    if endpoint.get("sticky_sessions"):
        body["session_id"] = trajectory_id
    return body, budget


def assistant_message(message, *, keep_responses_output=True):
    """Keep only complete, replayable assistant messages and their native calls."""
    if (
        not isinstance(message, dict)
        or not {"content", "tool_calls"} & message.keys()
        or not isinstance(message.get("content"), (str, type(None)))
    ):
        raise ValueError("Expected an assistant message")
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("Expected a list of native tool calls")
    clean = dict(role="assistant", content=message.get("content"))
    if isinstance(message.get("reasoning_content"), str):
        clean["reasoning_content"] = message["reasoning_content"]
    if calls:
        ids, normalized = set(), []
        for call in calls:
            if not isinstance(call, dict) or call.get("type", "function") != "function":
                raise ValueError("Expected native function calls")
            ident, function = call.get("id"), call.get("function")
            if (
                not isinstance(ident, str)
                or not ident
                or ident in ids
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not function["name"]
                or not isinstance(function.get("arguments"), str)
            ):
                raise ValueError("A native tool call needs a unique ID, name and arguments")
            ids.add(ident)
            normalized.append(
                dict(
                    id=ident,
                    type="function",
                    function=dict(
                        name=function["name"],
                        arguments=function["arguments"],
                    ),
                )
            )
        clean["tool_calls"] = normalized
    elif clean["content"] is None:
        clean["content"] = ""
    if keep_responses_output and isinstance(message.get(responses_api.REPLAY_ITEMS), list):
        clean[responses_api.REPLAY_ITEMS] = message[responses_api.REPLAY_ITEMS]
    return clean


class OpenAITransport:
    async def __call__(self, ticket):
        payload = ticket.payload
        endpoint = payload["endpoint_config"]
        use_responses = endpoint.get("use_responses_api", False)
        route = "/responses" if use_responses else "/chat/completions"
        # Credentials are process configuration; never put them in jobs/source.
        key = os.environ.get(key_variable(ticket.endpoint))
        headers = {"Authorization": "Bearer " + key} if key else {}
        body, budget = request_body(payload, ticket.trajectory_id)
        if token_limits.exceeds(budget):
            return {"context_limit": True, "local_budget": budget, "model": endpoint["model"]}
        timeout = endpoint.get("timeout_seconds", 120.0)
        # Bound the whole attempt as well as individual network operations.
        # Queue-owned retries each get their own deadline.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    endpoint["url"].rstrip("/") + route,
                    headers=headers,
                    json=body,
                )
        if response.status_code == 400 and any(
            s in response.text.lower()
            for s in ("context_length_exceeded", "maximum context length", "context window")
        ):
            return {"context_limit": True, "model": endpoint["model"]}
        response.raise_for_status()
        data = response.json()
        if use_responses:
            return responses_api.result(data, native="tools" in payload)
        choice = data["choices"][0]
        # Usage is kept even for unusable replies: their tokens were still billed.
        meta = dict(model=data.get("model"), usage=data.get("usage"))
        if "tools" in payload:
            finish = choice.get("finish_reason")
            if finish in {"stop", "tool_calls"}:
                try:
                    message = assistant_message(choice["message"], keep_responses_output=False)
                    if finish != "tool_calls" or message.get("tool_calls"):
                        return dict(meta, message=message)
                except (KeyError, ValueError):
                    pass
            return dict(meta, invalid_output=True, finish_reason=finish)
        # Incomplete text never leaves this local buffer; jobs never store it.
        if choice.get("finish_reason") != "stop":
            return dict(meta, invalid_output=True, finish_reason=choice.get("finish_reason"))
        text = (choice.get("message") or {}).get("content")
        if not isinstance(text, str):
            return dict(meta, invalid_output=True, finish_reason="stop")
        return dict(meta, text=text)


def invalid_reason(result):
    """Correction text for an unusable reply, sent back with the retry."""
    if result.get("finish_reason") == "length":
        return (
            "The previous reply was cut off at the output token limit before it finished. "
            "Write shorter notes that still cover the whole episode."
        )
    return "The previous reply had no text. Reply with the Draft notes as plain text."
