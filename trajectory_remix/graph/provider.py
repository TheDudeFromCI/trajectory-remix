"""Stateless OpenAI-compatible transport. Queue owns all retries and ordering."""

import json
import os

import httpx

from ..structured_output import decoding_schema


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
    schema = decoding_schema(payload["schema"])
    if strict_schema_supported(schema):
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
    messages = [
        *payload["messages"],
        {
            "role": "user",
            "content": "Return one JSON object matching this complete output schema. "
            "Do not include Markdown or explanations. Schema: " + json.dumps(payload["schema"]),
        },
    ]
    return {"type": "json_object"}, messages


class OpenAITransport:
    async def __call__(self, ticket):
        payload = ticket.payload
        endpoint = payload["endpoint_config"]
        # Credentials are process configuration; never put them in jobs/source.
        key = os.environ.get("REMIX_API_KEY_" + ticket.endpoint.upper().replace("-", "_"))
        headers = {"Authorization": "Bearer " + key} if key else {}
        response_format, messages = response_contract(payload)
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(
                endpoint["url"].rstrip("/") + "/chat/completions",
                headers=headers,
                json=dict(
                    model=endpoint["model"],
                    messages=messages,
                    stream=False,
                    response_format=response_format,
                ),
            )
        if response.status_code == 400 and any(
            s in response.text.lower()
            for s in ("context_length_exceeded", "maximum context length", "context window")
        ):
            return {"context_limit": True, "model": endpoint["model"]}
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            # Incomplete text never leaves this local buffer.
            return {"invalid_output": True, "model": data.get("model")}
        try:
            output = json.loads(choice["message"]["content"])
        except (ValueError, TypeError):
            return {"invalid_output": True, "model": data.get("model")}
        return dict(output=output, model=data.get("model"), usage=data.get("usage"))
