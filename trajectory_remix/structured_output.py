"""One native output contract for SDK streaming and Pydantic AI requests."""

from __future__ import annotations

import copy
import re

from pydantic_ai.output import OutputObjectDefinition


def decoding_schema(schema, local_constraints=None):
    """Project the decoding contract without changing the authoritative schema.

    Uniqueness across arbitrary array values is not supported by common grammar
    engines. The caller must still validate the original schema after inference.
    Walk only schema-valued keywords: a property named 'uniqueItems' and literal
    values in defaults/examples/const are user data and must survive unchanged.
    """
    result = copy.deepcopy(schema)
    maps = {'properties', 'patternProperties', '$defs', 'definitions',
            'dependentSchemas', 'dependencies'}
    single = {'additionalProperties', 'unevaluatedProperties', 'propertyNames',
              'items', 'additionalItems', 'contains', 'unevaluatedItems', 'not',
              'if', 'then', 'else', 'contentSchema'}
    arrays = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}

    def child_path(path, key):
        return path + '/' + str(key).replace('~', '~0').replace('/', '~1')

    def visit(node, path):
        if not isinstance(node, dict):
            return
        if 'uniqueItems' in node:
            unique = node.pop('uniqueItems')
            if unique is True and local_constraints is not None:
                local_constraints.append({'keyword': 'uniqueItems',
                                          'schema_path': child_path(path, 'uniqueItems')})
        for key, value in node.items():
            base = child_path(path, key)
            if key in maps and isinstance(value, dict):
                for name, child in value.items():
                    visit(child, child_path(base, name))
            elif key in arrays or (key == 'items' and isinstance(value, list)):
                if isinstance(value, list):
                    for index, child in enumerate(value):
                        visit(child, child_path(base, index))
            elif key in single:
                visit(value, base)

    visit(result, '')
    return result


def native_output(endpoint, schema, name='trajectory_content', *, local_constraints=None):
    """Preserve tool arguments and constraints, with uniqueness checked locally.

    OpenAI's strict-schema transformer closes arbitrary dictionaries and makes
    optional fields required. Local JSON-schema/grammar servers can enforce the
    decoding schema; never rewrite training decisions to fit that subset.
    ModelSession validates every result against the original complete schema.
    """
    if not schema or endpoint.get('structured_output', 'native') != 'native':
        return None
    return OutputObjectDefinition(
        json_schema=decoding_schema(schema, local_constraints),
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
