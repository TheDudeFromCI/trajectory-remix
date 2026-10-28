"""Proxy transport checks independent of the experimental request streams."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

MODULE = Path(__file__).with_name('agent_api_proxy.py')
spec = importlib.util.spec_from_file_location('agent_proxy_tested', MODULE)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)
TOKENIZER = Path(__file__).with_name('inference-tokenizer')


@pytest.mark.parametrize('stream', [False, True])
async def test_transport_and_mailbox_routing(tmp_path, stream):
    root = tmp_path / 'mailboxes'
    (root / 'assigned').mkdir(parents=True)
    (root / 'unrelated').mkdir()
    app = proxy.create_proxy(root, str(TOKENIZER))

    async def worker():
        path = root / 'assigned/000001.request.json'
        while not path.exists():
            await asyncio.sleep(.01)
        request = json.loads(path.read_text())
        assert request['messages'][0]['content'] == 'Return hello.'
        proxy.atomic_json(root / 'assigned/000001.response.json', {'content': 'hello'})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://proxy') as client:
        task = asyncio.create_task(worker())
        response = await client.post('/v1/chat/completions', json={'model': 'assigned', 'messages': [{'role': 'user', 'content': 'Return hello.'}], 'max_tokens': 32, 'stream': stream})
        await task
        assert response.status_code == 200
        if stream:
            chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
            assert chunks[0]['choices'][0]['delta']['content'] == 'hello'
            assert chunks[1]['choices'][0]['finish_reason'] == 'stop'
            assert 'data: [DONE]' in response.text
        else:
            assert response.json()['choices'][0]['message']['content'] == 'hello'
        record = json.loads((root / 'assigned/000001.record.json').read_text())
        assert record['usage']['total_tokens'] == record['usage']['prompt_tokens'] + record['usage']['completion_tokens']
        assert record['usage_source'] == 'surrogate-bpe-tokenizer'
        assert not list((root / 'unrelated').iterdir())
        missing = await client.post('/v1/chat/completions', json={'model': '../unrelated', 'messages': []})
        assert missing.status_code == 404
