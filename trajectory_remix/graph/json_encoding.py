"""JSON encoding for API responses that keeps every dict key.

FastAPI's ``jsonable_encoder`` defaults to ``sqlalchemy_safe=True``, which
silently drops any dict key starting with ``_sa`` (SQLAlchemy's internal
attributes). Remix doesn't use SQLAlchemy, and user data legitimately has such
keys: tokenizer vocabulary like ``_save``, trajectory metadata, tool arguments.

FastAPI has no setting for this, so ``install()`` points the encoder that
``fastapi.routing`` uses for endpoint return values at ``encode``.
"""

from functools import partial

import fastapi.routing
from fastapi.encoders import jsonable_encoder

encode = partial(jsonable_encoder, sqlalchemy_safe=False)


def install():
    """Make every FastAPI endpoint response keep keys starting with ``_sa``."""
    fastapi.routing.jsonable_encoder = encode
