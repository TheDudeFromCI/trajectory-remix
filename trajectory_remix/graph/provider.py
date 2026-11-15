"""Stateless OpenAI-compatible transport. Queue owns all retries and ordering."""
import json
import os

import httpx

from ..structured_output import decoding_schema


class OpenAITransport:
    async def __call__(self, ticket):
        payload = ticket.payload
        endpoint = payload['endpoint_config']
        # Credentials are process configuration; never put them in jobs/source.
        key = os.environ.get('REMIX_API_KEY_' + ticket.endpoint.upper().replace('-', '_'))
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(endpoint['url'].rstrip('/') + '/chat/completions', headers=headers,
                json=dict(model=endpoint['model'], messages=payload['messages'], stream=False,
                          response_format={'type': 'json_schema', 'json_schema': {
                              'name': 'graph_' + payload['operation'], 'strict': True, 'schema': decoding_schema(payload['schema'])}}))
        if response.status_code == 400 and any(s in response.text.lower() for s in ('context_length_exceeded', 'maximum context length', 'context window')):
            return {'context_limit': True, 'model': endpoint['model']}
        response.raise_for_status()
        data = response.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            # Incomplete text never leaves this local buffer.
            return {'invalid_output': True, 'model': data.get('model')}
        try:
            output = json.loads(choice['message']['content'])
        except (ValueError, TypeError):
            return {'invalid_output': True, 'model': data.get('model')}
        return dict(output=output, model=data.get('model'), usage=data.get('usage'))
