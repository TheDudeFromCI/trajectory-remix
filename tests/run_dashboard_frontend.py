"""Fresh-project browser workflow with a pausable fake provider."""

import asyncio
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from test_graph_generation import Fake

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


class PauseFixture(Fake):
    async def __call__(self, ticket):
        if ticket.epoch == 1:
            # Pause cancels this first request. The resumed worker runs in the next epoch.
            await asyncio.Event().wait()
        return await super().__call__(ticket)


def main():
    with tempfile.TemporaryDirectory(prefix="dashboard-ui-", dir="/tmp") as tmp:
        path = Path(tmp) / "project.sqlite"
        GraphStore.initialize(path)
        app = create_studio_app(path, artifact_root=Path(tmp) / "exports", provider=PauseFixture())
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
                    str(Path(__file__).with_name("dashboard_frontend.mjs")),
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
