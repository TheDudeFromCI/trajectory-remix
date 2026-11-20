"""One real loopback HTTP/stdio MCP exchange; no provider or compiler calls."""

import asyncio
import socket
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from trajectory_remix.graph.storage import GraphStore

LEGACY_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "transport-test", "version": "1"},
    },
}


@pytest.mark.asyncio
async def test_official_clients_share_graph_edits_and_origin_policy(tmp_path):
    pytest.importorskip("mcp")
    import httpx2
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    root = Path(__file__).parents[1]
    path = tmp_path / "transport.sqlite"
    GraphStore.initialize(path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "trajectory_remix",
            "graph-studio",
            "--dataset",
            str(path),
            "--port",
            str(port),
            "--mcp-allow-origin",
            "http://127.0.0.1:8123",
            cwd=root,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=5) as client:
                for _ in range(400):
                    try:
                        (await client.get("/")).raise_for_status()
                        break
                    except httpx.HTTPError:
                        assert process.returncode is None, (tmp_path / "server.log").read_text()
                        await asyncio.sleep(0.025)
                else:
                    pytest.fail("Tiny studio failed to start")
                blocked = await client.post(
                    "/mcp", headers={"Origin": "https://evil.example"}, json={}
                )
                assert blocked.status_code == 403
                preflight = await client.options(
                    "/mcp",
                    headers={
                        "Origin": "http://127.0.0.1:8123",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "mcp-session-id",
                    },
                )
                assert preflight.status_code == 200
                assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:8123"
                # Pre-2026 clients still get a stateful session from the manager.
                legacy = await client.post(
                    "/mcp",
                    headers={
                        "Origin": "http://127.0.0.1:8123",
                        "Accept": "application/json, text/event-stream",
                    },
                    json=LEGACY_INITIALIZE,
                )
                assert legacy.status_code == 200 and legacy.headers["mcp-session-id"]
                ident = str(uuid4())
                browser = httpx2.AsyncClient(
                    headers={"Origin": "http://127.0.0.1:8123"}, trust_env=False
                )
                async with (
                    browser,
                    streamable_http_client(base + "/mcp", http_client=browser) as (
                        read,
                        write,
                    ),
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        created = await session.call_tool(
                            "create_graph",
                            {
                                "format": "kidra-trajectory-graph/1",
                                "id": ident,
                                "windows": {
                                    "doc": {"purpose": "document", "agent_writable": False}
                                },
                                "events": {
                                    "a": {
                                        "kind": "external_text",
                                        "window": "doc",
                                        "content": "shared",
                                    }
                                },
                            },
                        )
                        assert not created.is_error
                        # The official client keeps GET/SSE open while POST calls work.
                        readback = await session.call_tool("read_graph", {"id": ident})
                        assert readback.structured_content["revision"] == 1
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "trajectory_remix.mcp_server", "--url", base],
                    cwd=str(root),
                )
                async with stdio_client(params) as streams, ClientSession(*streams) as session:
                    await session.initialize()
                    existing = await session.call_tool("read_graph", {"id": ident})
                    assert (
                        not existing.is_error
                        and existing.structured_content["source"]["events"]["a"]["content"]
                        == "shared"
                    )
                    patch = await session.call_tool(
                        "patch_graph_event",
                        {
                            "id": ident,
                            "expected_revision": 1,
                            "event_id": "a",
                            "patch": {"content": "updated"},
                        },
                    )
                    assert not patch.is_error and patch.structured_content["revision"] == 2
                    conflict = await session.call_tool(
                        "patch_graph_event",
                        {
                            "id": ident,
                            "expected_revision": 1,
                            "event_id": "a",
                            "patch": {"content": "stale"},
                        },
                    )
                    assert conflict.is_error
                assert GraphStore(path).read(ident).source.events["a"].content == "updated"
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
