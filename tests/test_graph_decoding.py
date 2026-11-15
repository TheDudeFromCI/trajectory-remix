"""Offline decoding projection regressions used by graph providers."""
import copy
import jsonschema
import pytest
from trajectory_remix.structured_output import decoding_schema


def test_decoding_projection_keeps_original_uniqueness_and_literal_keyword_data():
    schema = {'type': 'object', 'properties': {
        'uniqueItems': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True, 'maxItems': 4},
        'literal': {'const': {'uniqueItems': True}, 'default': {'uniqueItems': True},
                    'examples': [{'uniqueItems': True}], 'enum': [{'uniqueItems': True}]},
    }, 'required': ['uniqueItems'], 'additionalProperties': False,
        '$defs': {'uniqueItems': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'integer'}}},
        'allOf': [{'properties': {'other': {'$ref': '#/$defs/uniqueItems'}}}]}
    before = copy.deepcopy(schema)
    constraints = []
    projected = decoding_schema(schema, constraints)
    assert schema == before
    assert projected['properties']['uniqueItems'] == {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4}
    assert projected['properties']['literal'] == schema['properties']['literal']
    assert projected['$defs']['uniqueItems'] == {'type': 'array', 'items': {'type': 'integer'}}
    assert projected['allOf'] == schema['allOf']
    assert constraints == [
        {'keyword': 'uniqueItems', 'schema_path': '/properties/uniqueItems/uniqueItems'},
        {'keyword': 'uniqueItems', 'schema_path': '/$defs/uniqueItems/uniqueItems'},
    ]
    duplicate = {'uniqueItems': ['same', 'same']}
    jsonschema.validate(duplicate, projected)
    with pytest.raises(jsonschema.ValidationError, match='non-unique'):
        jsonschema.validate(duplicate, schema)


@pytest.mark.parametrize('keyword', ['items', 'prefixItems'])
def test_decoding_projection_handles_nested_array_schemas_and_escaped_paths(keyword):
    schema = {'type': 'array', keyword: [{'type': 'array', 'uniqueItems': True,
                                       'items': {'type': 'string'}}], 'uniqueItems': False}
    constraints = []
    projected = decoding_schema(schema, constraints)
    assert 'uniqueItems' not in projected and 'uniqueItems' not in projected[keyword][0]
    assert constraints == [{'keyword': 'uniqueItems', 'schema_path': f'/{keyword}/0/uniqueItems'}]
    original = {'properties': {'a~/b': {'type': 'array', 'uniqueItems': True}},
                'additionalProperties': False}
    constraints = []
    decoding_schema(original, constraints)
    assert constraints[0]['schema_path'] == '/properties/a~0~1b/uniqueItems'


