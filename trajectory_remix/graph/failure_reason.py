"""Short, content-free explanations of why a request or local step failed.

Job logs never carry prompts, model output, provider error bodies or keys. These
helpers keep that rule while still telling the reader *why* something failed: the
failure class, the HTTP status with a plain-language hint, validated provider
metadata, the endpoint host, and for local bugs the place in the code where the
error was raised.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PACKAGE = Path(__file__).resolve().parent.parent
# OpenAI-style error codes such as ``model_not_found``; free text never matches.
CODE = re.compile(r"[A-Za-z0-9_.:-]{1,60}")
PARAMETER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+|\[[0-9]+\])*")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

PARAMETER_HINTS = {
    "unknown_parameter": "the endpoint does not recognize this request field",
    "unsupported_parameter": "the endpoint or model does not support this request field",
    "unsupported_value": "the endpoint or model does not support the value for this request field",
    "invalid_value": "the endpoint rejected the value for this request field",
    "missing_required_parameter": "the endpoint requires this request field",
}

STATUS_HINTS = {
    400: "the endpoint rejected the request (check the model name, output mode and extra request fields)",
    401: "credentials were rejected",
    402: "the account has no credit or quota",
    403: "access is forbidden for these credentials or this model",
    404: "the URL or model was not found (check the base URL and model name)",
    408: "the endpoint timed out the request",
    413: "the request was too large for the endpoint",
    422: "the endpoint could not process the request body",
    429: "rate limited or out of quota; slow down or lower the concurrent-request limit",
    500: "the endpoint failed internally",
    502: "bad gateway between the endpoint and its backend",
    503: "the endpoint is overloaded or unavailable",
    504: "gateway timeout waiting on the model",
}


def key_variable(endpoint: str) -> str:
    """Environment variable that holds an endpoint's API key."""
    return "REMIX_API_KEY_" + endpoint.upper().replace("-", "_")


def origin(error: BaseException) -> str:
    """``file.py:line in function()`` for the innermost frame inside this package."""
    found = None
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if Path(code.co_filename).resolve().is_relative_to(PACKAGE):
            found = f"{Path(code.co_filename).name}:{tb.tb_lineno} in {code.co_name}()"
        tb = tb.tb_next
    return found or "unknown location"


def local_failure(error: BaseException) -> str:
    """Where and as what a local (non-provider) error was raised; no message text."""
    return f"{type(error).__name__} raised at {origin(error)}"


def log_local_error(error: BaseException, context: str, job: str) -> None:
    """Keep the full traceback in the server output; job logs only get the summary."""
    logging.getLogger("trajectory_remix.jobs").error(
        "%s failed for job %s", context, job, exc_info=error
    )


def provider_error(response: httpx.Response) -> tuple[str | None, str | None]:
    """Read only a short error identifier and a parameter path from the provider."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError, UnicodeError):
        return None, None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code") or error.get("type")
    param = error.get("param")
    if not isinstance(code, str) or not CODE.fullmatch(code):
        code = None
    if not isinstance(param, str) or len(param) > 120 or not PARAMETER.fullmatch(param):
        param = None
    return code, param


def host(config: dict) -> str:
    try:
        parts = urlsplit(config.get("url", ""))
        return parts.netloc.rpartition("@")[2] or "the endpoint"
    except ValueError:
        return "the endpoint"


def status_reason(error: httpx.HTTPStatusError, endpoint: str) -> str:
    status = error.response.status_code
    hint = STATUS_HINTS.get(status) or (
        "the endpoint reported a server error" if status >= 500 else "unexpected HTTP status"
    )
    if status in (401, 403) and not os.environ.get(key_variable(endpoint)):
        hint += f"; no key is set (export {key_variable(endpoint)})"
    code, param = provider_error(error.response)
    if status in (400, 422) and param:
        hint = PARAMETER_HINTS.get(code, hint)
    request_id = error.response.headers.get("x-request-id", "")
    return (
        f"HTTP {status} {error.response.reason_phrase or ''}".rstrip()
        + f": {hint}"
        + (f" [provider code {code}]" if code else "")
        + (f" [parameter {param}]" if param else "")
        + (f" [provider request ID {request_id}]" if REQUEST_ID.fullmatch(request_id) else "")
    )


def connect_reason(error: httpx.TransportError, where: str) -> str:
    cause = error.__cause__ or error.__context__
    detail = ""
    if isinstance(cause, OSError) and cause.strerror:
        detail = f" ({cause.strerror})"
    if isinstance(error, httpx.ConnectError):
        return f"could not connect to {where}{detail}; check the base URL and that the server is running"
    return f"the connection to {where} failed ({type(error).__name__}){detail}"


def endpoint_failure(error: BaseException, endpoint: str, config: dict | None = None) -> str:
    """Why one model request failed, in a line safe to keep in a job log."""
    config = config or {}
    where = host(config)
    if isinstance(error, httpx.HTTPStatusError):
        return status_reason(error, endpoint)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        limit = config.get("timeout_seconds")
        within = f" within {limit:g}s" if isinstance(limit, (int, float)) else ""
        return (
            f"no complete response from {where}{within}; the model may be overloaded or the "
            "endpoint's timeout setting too low"
        )
    if isinstance(error, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return "the base URL is not valid; it needs a scheme such as https://"
    if isinstance(error, httpx.TransportError):
        return connect_reason(error, where)
    if isinstance(error, (ValueError, UnicodeError)):
        route = "Responses" if config.get("use_responses_api") else "Chat Completions"
        return (
            f"the reply from {where} was not valid JSON; the URL may not be an OpenAI-compatible "
            f"{route} endpoint"
        )
    if isinstance(error, (KeyError, IndexError, TypeError)) and origin(error).startswith(
        "provider.py"
    ):
        return f"the reply from {where} lacked the expected chat-completion fields"
    return f"unexpected {local_failure(error)}"
