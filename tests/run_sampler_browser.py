"""Small scenario UI browser checks on an isolated, tokenizer-free graph project.

npm ci --prefix tests
CHROMIUM_PATH=/usr/bin/chromium .venv/bin/python tests/run_sampler_browser.py
Optionally set SAMPLER_SCREENSHOTS to capture review images.
"""
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


def main():
    with tempfile.TemporaryDirectory(prefix='sampler-browser-', dir='/tmp') as tmp:
        path = Path(tmp) / 'graph.sqlite'
        GraphStore.initialize(path)
        app = create_studio_app(path, artifact_root=Path(tmp) / 'exports')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(.01)
            if not server.started:
                raise RuntimeError('Test server did not start')
            subprocess.run(['node', str(Path(__file__).with_name('sampler_browser.mjs')), f'http://127.0.0.1:{port}'], check=True)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()


if __name__ == '__main__':
    main()
