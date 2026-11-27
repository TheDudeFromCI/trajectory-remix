"""Request-body validation errors that never echo the submitted body back.

FastAPI's default 422 response includes each failing field's ``input``. For a
settings save that input can be a multi-megabyte tokenizer definition, which
the dashboard then renders as one enormous error message. This handler keeps
FastAPI's ``{"detail": [...]}`` shape but drops ``input`` and ``ctx``.
"""

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_ECHOED_FIELDS = frozenset({"input", "ctx"})


def summarize(errors):
    """Return validation errors without the submitted values they refer to."""
    return [{k: v for k, v in error.items() if k not in _ECHOED_FIELDS} for error in errors]


def install(app: FastAPI):
    """Register the compact 422 handler for request validation on ``app``."""

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"detail": jsonable_encoder(summarize(error.errors()))}, 422)
