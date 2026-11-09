"""Optional Node/happy-dom integration test against a temporary local server.

Run npm ci --prefix tests first. These are DOM tests, not visual browser QA.
Pass a test filename to run a focused suite, e.g. editor-state.test.mjs.
"""

import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from tokenizers import Tokenizer
from trajectory_remix.compiler import Compiler
from trajectory_remix.storage import DatasetStore
from trajectory_remix.dataset import parse_import

root = Path(__file__).resolve().parents[1]
test_script = root / "tests" / (sys.argv[1] if len(sys.argv) > 1 else "frontend.test.mjs")
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory(prefix="remix-ui-") as temp:
    dataset = Path(temp) / "project.kidra-dataset"
    initial, _ = parse_import("sample.json", (root / "examples/reactive-sample.json").read_text())
    DatasetStore.initialize(dataset, Compiler(Tokenizer.from_file(str(root / "examples/demo-tokenizer.json")))).replace(initial, 0)
    server = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "trajectory_remix",
            "--dataset",
            str(dataset),
            "--port",
            str(port),
        ],
        cwd=root,
    )
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(base, timeout=1).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("Dashboard process exited before serving")
                time.sleep(0.1)
        else:
            raise RuntimeError("Dashboard did not become available")
        result = subprocess.run(
            ["node", str(test_script), base],
            check=False,
            cwd=root,
            timeout=60,
        )
        raise SystemExit(result.returncode)
    finally:
        server.terminate()
        server.wait(timeout=5)
