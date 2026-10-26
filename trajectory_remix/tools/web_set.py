"""Author-only readable-page fixtures for deterministic web research."""

from ._protocol import ToolError
from ._simulation import failure, result
from ._web import seed

name = "web_set"
invisible = True
description = "Replace the active synthetic web corpus and exact-query ranking overrides. Pages contain readable text, title, URL, optional publication date, links, status, latency or transport error. Old source references and in-flight results retain their snapshots. Never accesses the internet."
URL = {"type": "string", "minLength": 1, "maxLength": 2048}
schema = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array", "maxItems": 128,
            "items": {
                "type": "object",
                "properties": {
                    "url": URL,
                    "title": {"type": "string", "maxLength": 512},
                    "text": {"type": "string", "maxLength": 100000},
                    "published_at": {"type": "string", "maxLength": 64},
                    "status": {"type": "integer", "minimum": 200, "maximum": 599},
                    "latency_seconds": {"type": "number", "minimum": 0, "maximum": 86400},
                    "error": {"type": "string", "enum": ["dns", "connection_refused", "connection_reset", "tls", "timeout"]},
                    "links": {
                        "type": "array", "maxItems": 128,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "text": {"type": "string", "maxLength": 512},
                                "url": URL,
                            },
                            "required": ["id", "text", "url"], "additionalProperties": False,
                        },
                    },
                },
                "required": ["url", "title", "text"], "additionalProperties": False,
            },
        },
        "searches": {
            "type": "array", "maxItems": 128,
            "items": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 512},
                    "urls": {"type": "array", "maxItems": 128, "items": URL},
                },
                "required": ["query", "urls"], "additionalProperties": False,
            },
        },
        "search_latency_seconds": {"type": "number", "minimum": 0, "maximum": 86400},
    },
    "required": ["pages"], "additionalProperties": False,
}


def run(args, state, windows):
    try:
        return result(seed(args, state), modified=True)
    except ToolError as error:
        return failure(str(error))
