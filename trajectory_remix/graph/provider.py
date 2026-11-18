"""Stateless OpenAI-compatible transport. Queue owns all retries and ordering."""

import asyncio
import json
import math
import os

import httpx

from ..structured_output import decoding_schema
from .failure_reason import key_variable


def strict_schema_supported(schema):
    """Conservative common strict-schema subset; arbitrary patches use JSON mode.

    Decoding is only a convenience. The original local schema remains authoritative.
    In particular, arbitrary tool arguments and root repair unions are valid graph
    contracts but are not valid OpenAI strict response schemas.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False

    def supported(node):
        if (
            not isinstance(node, dict)
            or not node
            or any(
                k in node
                for k in (
                    "oneOf",
                    "allOf",
                    "not",
                    "patternProperties",
                    "$ref",
                    "if",
                    "then",
                    "else",
                )
            )
        ):
            return False
        if "anyOf" in node:
            return all(supported(child) for child in node["anyOf"])
        kind = node.get("type")
        if kind == "object":
            props = node.get("properties", {})
            return (
                node.get("additionalProperties") is False
                and set(node.get("required", [])) == set(props)
                and all(supported(value) for value in props.values())
            )
        if kind == "array":
            return supported(node.get("items"))
        return kind in ("string", "number", "integer", "boolean", "null")

    return supported(schema)


def response_contract(payload):
    """(response_format, messages) for a request. response_format None means omit it.

    The endpoint's output_mode chooses: "auto" uses native strict JSON schema when the
    schema allows it and JSON mode otherwise; "json_object" always uses JSON mode with the
    schema in the prompt; "prompt" puts the schema in the prompt and sends no
    response_format, for servers that support neither.
    """
    mode = (payload.get("endpoint_config") or {}).get("output_mode", "auto")
    schema = decoding_schema(payload["schema"])
    if mode == "auto" and strict_schema_supported(schema):
        return (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "graph_" + payload["operation"],
                    "strict": True,
                    "schema": schema,
                },
            },
            payload["messages"],
        )
    # This is a wire-format choice, not a retry, alternate model or endpoint.
    # JSON mode still receives the complete schema and is validated after return.
    # The schema never changes between calls, so it goes right after the system prompt,
    # ahead of the per-call data, where a prefix cache can reuse it. It is joined to the
    # first user message rather than sent separately: some open-weight chat templates
    # reject two user messages in a row.
    contract = (
        "Return one JSON object matching this complete output schema. "
        "Do not include Markdown or explanations. Schema: " + json.dumps(payload["schema"])
    )
    messages = [dict(m) for m in payload["messages"]]
    first = next((m for m in messages if m["role"] != "system"), None)
    if first is not None and first["role"] == "user":
        first["content"] = contract + "\n\n" + first["content"]
    else:
        at = messages.index(first) if first is not None else len(messages)
        messages.insert(at, {"role": "user", "content": contract})
    return (None if mode == "prompt" else {"type": "json_object"}), messages


def context_budget(payload, response_format, messages):
    """Conservative byte-token ceiling, independent of training tokenizers.

    UTF-8 bytes bound byte-backed BPE input tokens; reserve additional template
    overhead. This deliberately rejects some requests that a model tokenizer
    could fit. Never truncate evidence to make a request appear to fit.
    """
    endpoint = payload["endpoint_config"]
    limit = endpoint.get("context_window_tokens", 65536)
    output = endpoint.get("max_output_tokens", 8192)
    ratio = endpoint.get("bytes_per_token", 1.0)
    size = len(
        json.dumps(
            dict(messages=messages, response_format=response_format), ensure_ascii=False
        ).encode()
    )
    return dict(
        input_bytes=size,
        bytes_per_token=ratio,
        input_tokens_estimate=math.ceil(size / ratio),
        input_ceiling=limit - output - 512,
        context_window_tokens=limit,
        max_output_tokens=output,
    )


def request_body(payload, trajectory_id):
    """The exact HTTP body for a request (no credentials) and its context budget."""
    endpoint = payload["endpoint_config"]
    response_format, messages = response_contract(payload)
    budget = context_budget(payload, response_format, messages)
    body = dict(
        # Provider options first; validation stops them replacing the fields below.
        **endpoint.get("extra_body", {}),
        model=endpoint["model"],
        messages=messages,
        stream=False,
        max_tokens=budget["max_output_tokens"],
        **({"response_format": response_format} if response_format else {}),
    )
    for key in ("temperature", "top_p", "presence_penalty"):
        if endpoint.get(key) is not None:
            body[key] = endpoint[key]
    if endpoint.get("sticky_sessions"):
        body["session_id"] = trajectory_id
    return body, budget


class OpenAITransport:
    async def __call__(self, ticket):
        payload = ticket.payload
        endpoint = payload["endpoint_config"]
        # Credentials are process configuration; never put them in jobs/source.
        key = os.environ.get(key_variable(ticket.endpoint))
        headers = {"Authorization": "Bearer " + key} if key else {}
        body, budget = request_body(payload, ticket.trajectory_id)
        if budget["input_tokens_estimate"] > budget["input_ceiling"]:
            return {"context_limit": True, "local_budget": budget, "model": endpoint["model"]}
        timeout = endpoint.get("timeout_seconds", 120.0)
        # Bound the whole attempt as well as individual network operations.
        # Queue-owned retries each get their own deadline.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    endpoint["url"].rstrip("/") + "/chat/completions",
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
        choice = data["choices"][0]
        # Usage is kept even for unusable replies: their tokens were still billed.
        meta = dict(model=data.get("model"), usage=data.get("usage"))
        # Incomplete text never leaves this local buffer; jobs never store it.
        if choice.get("finish_reason") != "stop":
            return dict(meta, invalid_output=True, finish_reason=choice.get("finish_reason"))
        try:
            output = json.loads(choice["message"]["content"])
        except (ValueError, TypeError):
            return dict(meta, invalid_output=True, finish_reason="stop")
        return dict(meta, output=output)


def invalid_reason(result):
    """Correction text for an unusable reply, sent back with the retry."""
    if result.get("finish_reason") == "length":
        return (
            "The previous reply was cut off at the output token limit before it finished. "
            "Return a shorter, complete JSON object."
        )
    return "The previous reply was not a single valid JSON object. Return only that object."
