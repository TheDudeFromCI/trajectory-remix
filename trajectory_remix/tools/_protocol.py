from __future__ import annotations
import json
import re


class ToolError(ValueError):
    def __init__(self, message, conversation_id=None):
        super().__init__(message)
        self.conversation_id = conversation_id


def tool_failure(error):
    outcome = {"ok": False, "error": str(error)}
    cid = getattr(error, "conversation_id", None)
    if cid is not None:
        outcome["conversationId"] = cid
    return outcome


def validate_subagent_outcome(outcome):
    """Validate an author-supplied reply, never a request to a model provider."""
    if not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool:
        raise ToolError("Recorded outcome requires an ok boolean")
    result = outcome.get("result")
    if outcome["ok"]:
        if not isinstance(result, dict) or not isinstance(result.get("response"), str):
            raise ToolError("Subagent outcome requires result.response text")
        if not result["response"].strip() or len(result["response"]) > 200_000:
            raise ToolError(
                "Subagent must return nonempty text of at most 200,000 characters"
            )
    elif not isinstance(outcome.get("error"), str):
        raise ToolError("Failed outcome requires error text")
    for container in (outcome, result if isinstance(result, dict) else {}):
        if "conversationId" in container and (
            not isinstance(container["conversationId"], str)
            or not container["conversationId"]
            or len(container["conversationId"]) > 128
        ):
            raise ToolError("Invalid recorded conversationId")
    if len(json.dumps(outcome).encode()) > 200_000:
        raise ToolError("Recorded outcome exceeds 200,000 bytes")


def tool_text(text, special_tokens, *, json_encoded=False):
    """Keep tool data from becoming model-owned control tokens."""
    escapes = {}
    for spelling in special_tokens:
        units = spelling.encode("utf-16-be")
        key = (
            json.dumps(spelling, ensure_ascii=False)[1:-1] if json_encoded else spelling
        )
        escapes[key] = "".join(
            f"\\u{int.from_bytes(units[i : i + 2], 'big'):04x}"
            for i in range(0, len(units), 2)
        )
    pattern = "|".join(re.escape(s) for s in sorted(escapes, key=len, reverse=True))
    return re.sub(pattern, lambda m: escapes[m[0]], text) if pattern else text


def tool_json(value, special_tokens):
    """Serialize observations/calls without embedding model control tokens."""
    return tool_text(
        json.dumps(value, ensure_ascii=False), special_tokens, json_encoded=True
    )


def chat_context(windows, metadata):
    """Per-call trajectory context; standalone sandboxes have no chat window."""
    return {
        "chat_open": any(w["id"] == "chat" and w["close"] is None for w in windows),
        "agent_name": (
            metadata.get("agent_name", "Kidra")
            if isinstance(metadata, dict)
            else "Kidra"
        ),
    }


def tool_message(tag, value, special_tokens):
    """Keep JSON data from introducing protocol tags or model control tokens."""
    payload = tool_json(value, special_tokens).replace("<", "\\u003c")
    return f"<{tag}>{payload}</{tag}>"


def tool_request(tool, arguments, special_tokens):
    return tool_message("tool", {"tool": tool, "args": arguments}, special_tokens)
