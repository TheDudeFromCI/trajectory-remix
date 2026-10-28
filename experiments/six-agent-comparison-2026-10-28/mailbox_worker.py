#!/usr/bin/env python3
"""Read one assigned API request or atomically submit its text response."""
import argparse
import json
import time
from pathlib import Path
from tokenizers import Tokenizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mailbox', type=Path, required=True)
    parser.add_argument('action', choices=['next', 'respond'])
    parser.add_argument('--request')
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    folder = args.mailbox
    if args.action == 'next':
        (folder / 'ready.json').write_text(json.dumps({'ready_at': time.time()}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for path in sorted(folder.glob('*.request.json')):
                ident = path.name.split('.')[0]
                if not (folder / f'{ident}.response.json').exists():
                    (folder / f'{ident}.claim.json').write_text(json.dumps({'claimed_at': time.time()}))
                    request = json.loads(path.read_text())
                    print(json.dumps({'request_id': ident, 'max_tokens': request.get('max_completion_tokens', request.get('max_tokens', 1024)), 'messages': request['messages']}, ensure_ascii=False))
                    return
            if (folder / 'terminal.json').exists():
                print('DONE')
                return
            time.sleep(.25)
        print('WAIT: poll again')
    else:
        if not args.request or not args.file:
            parser.error('respond requires --request and --file')
        request = json.loads((folder / f'{args.request}.request.json').read_text())
        text = args.file.read_text()
        tokenizer = Tokenizer.from_file(str(folder / 'tokenizer.json'))
        count = len(tokenizer.encode(text, add_special_tokens=False).ids)
        cap = request.get('max_completion_tokens', request.get('max_tokens', 1024))
        if count > cap:
            raise SystemExit(f'Answer uses {count} surrogate tokens; reduce to at most {cap}, preserving required fields.')
        output = folder / f'{args.request}.response.json'
        temporary = output.with_suffix('.tmp')
        temporary.write_text(json.dumps({'content': text, 'worker_finished_at': time.time()}, ensure_ascii=False))
        temporary.replace(output)
        print(f'SUBMITTED {args.request}: {count} surrogate output tokens')


if __name__ == '__main__':
    main()
