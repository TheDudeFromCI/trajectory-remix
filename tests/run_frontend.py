"""Optional Node/happy-dom integration test against a temporary local server.

Run npm install --prefix tests first. These are DOM tests, not visual browser QA.
"""
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
base = f'http://127.0.0.1:{port}'
with tempfile.TemporaryDirectory(prefix='remix-ui-') as temp:
    server = subprocess.Popen([sys.executable, '-u', '-m', 'trajectory_remix', '--tokenizer', 'examples/demo-tokenizer.json', '--dataset', 'examples/reactive-sample.json', '--port', str(port), '--data-dir', temp], cwd=root)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base, timeout=1).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Dashboard process exited before serving')
                time.sleep(.1)
        else:
            raise RuntimeError('Dashboard did not become available')
        result = subprocess.run(['node', str(root/'tests/frontend.test.mjs'), base], cwd=root, timeout=30)
        raise SystemExit(result.returncode)
    finally:
        server.terminate()
        server.wait(timeout=5)
