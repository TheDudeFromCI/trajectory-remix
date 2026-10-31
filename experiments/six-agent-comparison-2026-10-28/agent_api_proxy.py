#!/usr/bin/env python3
"""Loopback OpenAI-compatible API served by isolated filesystem worker mailboxes.
Workers provide all text; usage is a declared surrogate BPE count, not billing usage.
"""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from transformers import AutoTokenizer


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def create_proxy(mailboxes, tokenizer_dir, timeout=570):
    root = Path(mailboxes).resolve()
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    routes = {p.name: p for p in root.iterdir() if p.is_dir()}
    counts = {name: 0 for name in routes}
    app = FastAPI()

    @app.get('/health')
    def health():
        return {'ready': True, 'worker_count': len(routes)}

    @app.get('/v1/models')
    def models():
        return {'object': 'list', 'data': [{'id': route, 'object': 'model'} for route in routes]}

    @app.post('/v1/chat/completions')
    async def complete(request: Request):
        raw = await request.body()
        if len(raw) > 4_000_000:
            raise HTTPException(413, 'Request exceeds 4 MB')
        body = json.loads(raw)
        route = body.get('model')
        if route not in routes:
            raise HTTPException(404, 'Unknown worker route')
        counts[route] += 1
        ident = f'{counts[route]:06d}'
        folder = routes[route]
        record_path = folder / f'{ident}.record.json'
        record = {'id': ident, 'received_at': time.time(), 'request': body,
                  'usage_source': 'surrogate-bpe-tokenizer', 'reasoning_tokens': None}
        atomic_json(record_path, record)
        atomic_json(folder / f'{ident}.request.json', body)
        deadline = time.monotonic() + timeout
        response_path = folder / f'{ident}.response.json'
        while not response_path.exists():
            if time.monotonic() > deadline:
                record.update(finished_at=time.time(), error='Worker response timeout')
                atomic_json(record_path, record)
                raise HTTPException(504, 'Worker response timeout')
            await asyncio.sleep(.1)
        response = json.loads(response_path.read_text())
        text = response.get('content')
        if not isinstance(text, str):
            raise HTTPException(502, 'Worker must return text content')
        cap = int(body.get('max_completion_tokens', body.get('max_tokens', 1024)))
        output_ids = tokenizer.encode(text, add_special_tokens=False)
        finish = 'stop' if len(output_ids) <= cap else 'length'
        served = text if finish == 'stop' else tokenizer.decode(output_ids[:cap], skip_special_tokens=False)
        prompt = len(tokenizer.apply_chat_template(body['messages'], tokenize=True, add_generation_prompt=True, return_dict=False))
        completion = min(len(output_ids), cap)
        usage = {'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': prompt + completion}
        claim = folder / f'{ident}.claim.json'
        record.update(response=response, served_content=served, usage=usage, finish_reason=finish,
                      finished_at=time.time(), worker_claim=json.loads(claim.read_text()) if claim.exists() else None)
        record['turnaround_seconds'] = record['finished_at'] - record['received_at']
        atomic_json(record_path, record)
        common = {'id': 'chatcmpl-' + uuid.uuid4().hex, 'created': int(time.time()), 'model': route}
        if body.get('stream'):
            async def events():
                for chunk in [
                    {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': served}, 'finish_reason': None}]},
                    {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': usage},
                ]:
                    yield 'data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n'
                yield 'data: [DONE]\n\n'
            return StreamingResponse(events(), media_type='text/event-stream')
        return {**common, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': served}, 'finish_reason': finish}], 'usage': usage}
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mailboxes', type=Path, required=True)
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8791)
    args = parser.parse_args()
    uvicorn.run(create_proxy(args.mailboxes, str(args.tokenizer)), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
