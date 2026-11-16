"""Real-browser Flow tab checks with an injected fake Generator."""

import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from test_graph_generation import Fake, settings

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


def main():
    with tempfile.TemporaryDirectory(prefix="graph-flow-ui-", dir="/tmp") as tmp:
        path = Path(tmp) / "project.sqlite"
        GraphStore.initialize(path, settings(fast=False, critical=False))
        app = create_studio_app(path, artifact_root=Path(tmp) / "exports", provider=Fake())
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(0.01)
            if not server.started:
                raise RuntimeError("Studio did not start")
            subprocess.run(
                [
                    "node",
                    str(Path(__file__).with_name("flow_browser_frontend.mjs")),
                    f"http://127.0.0.1:{sock.getsockname()[1]}",
                ],
                check=True,
            )
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()


if __name__ == "__main__":
    main()
