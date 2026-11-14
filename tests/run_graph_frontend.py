"""Run the small graph DOM/API suite against one temporary local studio."""
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app
from test_graph_compiler import profile


def main(script='graph_frontend.mjs'):
    with tempfile.TemporaryDirectory(prefix='graph-dom-', dir='/tmp') as tmp:
        path = Path(tmp) / 'graph.sqlite'
        GraphStore.initialize(path, {'tokenizer_profiles': {'bytes': profile().model_dump(mode='json')}})
        app = create_studio_app(path, artifact_root=Path(tmp) / 'exports')
        sock = socket.socket(); sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True); thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(.01)
            if not server.started:
                raise RuntimeError('Test server did not start')
            subprocess.run(['node', str(Path(__file__).with_name(script)), f'http://127.0.0.1:{port}'], check=True)
        finally:
            server.should_exit = True; thread.join(timeout=5); sock.close()


if __name__ == '__main__':
    main()
