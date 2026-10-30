"""One native output contract for SDK streaming and Pydantic AI requests."""

from __future__ import annotations

import copy
import re

from pydantic_ai.output import OutputObjectDefinition


def native_output(endpoint, schema, name='trajectory_content'):
    """Keep scenario schemas intact, including open tool-argument objects.

    OpenAI's strict-schema transformer closes arbitrary dictionaries and makes
    optional fields required. Local JSON-schema/grammar servers can enforce the
    original schema; never rewrite the training decisions to fit that subset.
    """
    if not schema or endpoint.get('structured_output', 'native') != 'native':
        return None
    return OutputObjectDefinition(
        json_schema=copy.deepcopy(schema),
        name=re.sub(r'[^a-zA-Z0-9_-]', '_', name or 'trajectory_content')[:64],
        strict=True,
    )


def response_format(output):
    """The Chat Completions representation of Pydantic AI's native output."""
    if output is None:
        return None
    return {'type': 'json_schema', 'json_schema': {
        'name': output.name, 'schema': output.json_schema, 'strict': output.strict,
    }}
