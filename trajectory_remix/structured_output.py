"""One native output contract for SDK streaming and Pydantic AI requests."""

from __future__ import annotations

import copy
import re
from urllib.parse import unquote

from pydantic_ai.output import OutputObjectDefinition


def decoding_schema(schema, local_constraints=None):
    """Project the decoding contract without changing the authoritative schema.

    Array uniqueness and conditional rules are checked against the original
    schema after inference. The projection must accept every originally valid
    value, including under negation, exclusive choices and references.
    Walk only schema-valued keywords: a property named 'if' and literal
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

    def children(node):
        for key, value in node.items():
            if key in maps and isinstance(value, dict):
                for name, child in value.items():
                    yield (key, name), child
            elif key in arrays or (key == 'items' and isinstance(value, list)):
                if isinstance(value, list):
                    for index, child in enumerate(value):
                        yield (key, index), child
            elif key in single:
                yield (key,), value

    def has_local_rules(node, seen=frozenset(), *, follow_refs=True):
        if not isinstance(node, dict) or id(node) in seen:
            return False
        seen = seen | {id(node)}
        if node.get('uniqueItems') is True or any(key in node for key in ('if', 'then', 'else')):
            return True
        ref = node.get('$ref')
        if follow_refs and isinstance(ref, str) and ref.startswith('#'):
            target = schema
            try:
                fragment = unquote(ref[1:])
                if fragment and not fragment.startswith('/'):
                    raise KeyError(ref)
                for part in fragment[1:].split('/') if fragment else []:
                    part = part.replace('~1', '/').replace('~0', '~')
                    target = target[int(part)] if isinstance(target, list) else target[part]
            except (KeyError, IndexError, TypeError, ValueError):
                target = None
            if target is None:
                # Anchors/dynamic scopes cannot be resolved by a JSON pointer.
                # Conservatively account for any rule changed in this document.
                return has_local_rules(schema, follow_refs=False)
            if has_local_rules(target, seen):
                return True
        return any(has_local_rules(child, seen, follow_refs=follow_refs) for _, child in children(node))

    def record(key, path):
        if local_constraints is not None:
            local_constraints.append({'keyword': key, 'schema_path': child_path(path, key)})

    def visit(node, path):
        if not isinstance(node, dict):
            return
        relaxed = has_local_rules(node)
        exclusive_choice = any(has_local_rules(child) for child in node.get('oneOf', []))
        if 'uniqueItems' in node and node.pop('uniqueItems') is True:
            record('uniqueItems', path)
        # These keywords form one rule. Keeping a detached branch could change
        # its meaning or expose another unsupported keyword to the backend.
        for key in ('if', 'then', 'else'):
            if key in node:
                node.pop(key)
                record(key, path)
        # Relaxing a negated schema would otherwise strengthen the wire schema.
        if 'not' in node and has_local_rules(node['not']):
            visit(node.pop('not'), child_path(path, 'not'))
            record('not', path)
        if 'maxContains' in node and has_local_rules(node.get('contains')):
            node.pop('maxContains')
            record('maxContains', path)
        # Conditional branches can supply evaluation annotations. Removing them
        # must not make previously valid properties/items become "unevaluated".
        for key in ('unevaluatedProperties', 'unevaluatedItems'):
            if relaxed and key in node:
                visit(node.pop(key), child_path(path, key))
                record(key, path)
        for parts, child in list(children(node)):
            location = path
            for part in parts:
                location = child_path(location, part)
            visit(child, location)
        if exclusive_choice:
            choice = {'anyOf': node.pop('oneOf')}
            if 'anyOf' in node:
                node.setdefault('allOf', []).append(choice)
            else:
                node.update(choice)
            record('oneOf', path)
        # Do not send empty allOf lists or tautological wrappers left after
        # removing conditional-only branches (as used by the standard idle flow).
        if 'allOf' in node:
            node['allOf'] = [child for child in node['allOf'] if child != {} and child is not True]
            if not node['allOf']:
                node.pop('allOf')

    visit(result, '')
    return result


def native_output(endpoint, schema, name='trajectory_content', *, local_constraints=None):
    """Preserve structure; check uniqueness/conditionals against the full schema.

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
