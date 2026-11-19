"""The dashboard's MCP endpoint serves the 2026-07-28 protocol through the official client."""

from uuid import uuid4

import pytest

from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.mcp_server import CommandBridge


def studio(tmp_path):
    from trajectory_remix.graph.studio_api import create_studio_app

    path = tmp_path / "modern.sqlite"
    GraphStore.initialize(path)
    return create_studio_app(path, artifact_root=tmp_path / "exports")


def client_for(app, origin=None):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        headers={"Origin": origin} if origin else None,
    )
    return http, Client(streamable_http_client("http://127.0.0.1/mcp", http_client=http))


@pytest.mark.asyncio
async def test_auto_mode_client_negotiates_modern_protocol_and_calls_tools(tmp_path):
    pytest.importorskip("mcp")
    app = studio(tmp_path)
    ident = str(uuid4())
    async with app.router.lifespan_context(app):
        http, client = client_for(app)
        async with http, client:
            assert client.protocol_version == "2026-07-28"
            listing = await client.list_tools()
            assert "read_graph" in {tool.name for tool in listing.tools}
            created = await client.call_tool(
                "create_graph",
                {"format": "kidra-trajectory-graph/1", "id": ident, "windows": {}, "events": {}},
            )
            assert not created.is_error, created.structured_content
            missing = await client.call_tool("read_graph", {"id": str(uuid4())})
            assert missing.is_error and missing.structured_content["detail"]


@pytest.mark.asyncio
async def test_modern_protocol_still_honours_the_origin_guard(tmp_path):
    pytest.importorskip("mcp")
    app = studio(tmp_path)
    async with app.router.lifespan_context(app):
        http, _ = client_for(app, origin="https://evil.example")
        async with http:
            # A 2026-07-28 request carries no session; the guard must still run first.
            response = await http.post(
                "http://127.0.0.1/mcp",
                headers={"Mcp-Protocol-Version": "2026-07-28"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
    assert response.status_code == 403
    assert "--mcp-allow-origin" in response.json()["detail"]


def test_command_bridge_input_schema_lookup():
    class Commands:
        def tools(self):
            return [{"name": "a", "description": "", "inputSchema": {"type": "object"}}]

    bridge = CommandBridge(Commands())
    assert bridge.input_schema("a") == {"type": "object"}
    assert bridge.input_schema("missing") is None
