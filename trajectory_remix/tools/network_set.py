"""Author-only HTTP fixtures for deterministic network simulation."""

import copy
import json
from ._simulation import result, failure
from .runtime import execution_context

name = "network_set"
invisible = True
description = "Replace simulated HTTP routes and reset response cursors. Routes match method and URL glob, optionally request_body and request_headers. Responses form a sequence (last repeats), with status/headers/body, latency_seconds or error. In-flight requests retain their sampled response. No real network access."
HEADERS = {
    "type": "object",
    "maxProperties": 64,
    "additionalProperties": {"type": "string", "maxLength": 4096},
}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
schema = {
    "type": "object",
    "properties": {
        "routes": {
            "type": "array",
            "maxItems": 128,
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "minLength": 1, "maxLength": 2048},
                    "method": {"type": "string", "enum": METHODS},
                    "request_body": {"type": "string", "maxLength": 100000},
                    "request_headers": HEADERS,
                    "responses": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 128,
                        "items": {
                            "type": "object",
                            "properties": {
                                "status": {
                                    "type": "integer",
                                    "minimum": 100,
                                    "maximum": 599,
                                },
                                "headers": HEADERS,
                                "body": {"type": "string", "maxLength": 100000},
                                "latency_seconds": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 86400,
                                },
                                "error": {
                                    "type": "string",
                                    "enum": [
                                        "dns",
                                        "connection_refused",
                                        "connection_reset",
                                        "tls",
                                        "timeout",
                                    ],
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["url", "responses"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["routes"],
    "additionalProperties": False,
}


def run(args, state, windows):
    config = {"routes": copy.deepcopy(args["routes"]), "cursors": {}}
    if (
        len(json.dumps([config, state.get("network_pending", {})]).encode())
        > execution_context.get().max_bytes
    ):
        return failure("Network fixture quota exceeded")
    state["network"] = config
    return result({"routes": len(config["routes"])}, modified=True)
