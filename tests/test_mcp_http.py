import asyncio
import builtins
import socket
import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp", reason="Install .[mcp,test] for transport tests")

from trajectory_remix.mcp_server import browser_origin
from trajectory_remix.server import create_app
from trajectory_remix.storage import DatasetStore
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

ACCEPT = {"Accept": "application/json, text/event-stream"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "direct-http-test", "version": "1"},
    },
}


@pytest.fixture
async def http_studio(tmp_path, compiler):
    project = tmp_path / "http.sqlite"
    DatasetStore.initialize(project, compiler)
    app = create_app(project, mcp_allowed_origins=["http://127.0.0.1:8123"])
    ready, stopped = asyncio.Event(), asyncio.Event()

    async def lifespan():
        async with app.router.lifespan_context(app):
            ready.set()
            await stopped.wait()

    task = asyncio.create_task(lifespan())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8765",
            headers=ACCEPT,
        ) as client:
            yield app, client
    finally:
        stopped.set()
        await task


async def initialize(client, **kwargs):
    response = await client.post("/mcp", json=INITIALIZE, **kwargs)
    assert response.status_code == 200, response.text
    assert response.json()["jsonrpc"] == "2.0" and response.json()["id"] == 1
    assert response.json()["result"]["protocolVersion"] == "2025-03-26"
    session = response.headers["mcp-session-id"]
    headers = {"Mcp-Session-Id": session, "Mcp-Protocol-Version": "2025-03-26"}
    response = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert response.status_code == 202
    return headers


async def test_direct_post_initialize_discovery_calls_and_session_lifecycle(
    http_studio,
):
    app, client = http_studio
    # Reproduce the original symptom: root is HTML, not the MCP endpoint.
    root = await client.post("/", json=INITIALIZE)
    assert root.status_code == 405 and root.headers["allow"] == "GET"
    assert (await client.get("/")).status_code == 200
    headers = await initialize(client)
    listed = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert listed.status_code == 200
    assert "get_authoring_guide" in {
        t["name"] for t in listed.json()["result"]["tools"]
    }
    created = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_trajectory",
                "arguments": {"sample_id": "http-authored"},
            },
        },
    )
    result = created.json()["result"]
    assert created.status_code == 200 and not result["isError"]
    assert result["structuredContent"]["sample_id"] == "http-authored"
    assert app.state.dataset["samples"][0]["id"] == "http-authored"
    invalid = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_trajectory",
                "arguments": {"sample_id": "missing"},
            },
        },
    )
    assert invalid.status_code == 200 and invalid.json()["result"]["isError"]
    assert invalid.json()["result"]["structuredContent"]["code"] == "not_found"
    deleted = await client.delete("/mcp", headers=headers)
    assert deleted.status_code == 200
    assert (
        await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        )
    ).status_code == 404


async def test_http_headers_routing_and_protocol_errors(http_studio):
    _, client = http_studio
    assert (
        await client.post("/mcp", json=INITIALIZE, headers={"Accept": "text/plain"})
    ).status_code == 406
    malformed = await client.post(
        "/mcp", content="not json", headers={"Content-Type": "application/json"}
    )
    assert malformed.status_code == 400 and malformed.json()["jsonrpc"] == "2.0"
    assert (await client.put("/mcp", json=INITIALIZE)).status_code == 405
    assert (
        await client.get("/mcp", headers={"Accept": "text/event-stream"})
    ).status_code == 400
    assert (
        await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
    ).status_code == 400
    headers = await initialize(client)
    unsupported = await client.post(
        "/mcp",
        headers={**headers, "Mcp-Protocol-Version": "invalid"},
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    )
    assert unsupported.status_code == 400
    unknown = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 4, "method": "unknown/method"},
    )
    assert unknown.status_code == 200 and unknown.json()["id"] == 4
    assert unknown.json()["error"]["code"] in (-32601, -32602)


async def test_cors_exposes_session_headers_and_does_not_weaken_dashboard_guard(
    http_studio,
):
    _, client = http_studio
    origin = "http://127.0.0.1:8123"
    preflight = await client.options(
        "/mcp",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,mcp-session-id,mcp-protocol-version",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "POST" in preflight.headers["access-control-allow-methods"]
    response = await client.post("/mcp", json=INITIALIZE, headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "Mcp-Session-Id" in response.headers["access-control-expose-headers"]
    assert (
        await client.post(
            "/mcp", json=INITIALIZE, headers={"Origin": "https://hostile.example"}
        )
    ).status_code == 403
    assert (
        await client.post("/mcp", json=INITIALIZE, headers={"Host": "hostile.example"})
    ).status_code == 400
    assert (
        await client.get("/api/state", headers={"Origin": origin})
    ).status_code == 403
    assert (
        "access-control-allow-origin"
        not in (await client.get("/api/state", headers={"Origin": origin})).headers
    )


async def test_browser_origins_are_opt_in(tmp_path, compiler):
    project = tmp_path / "default.sqlite"
    DatasetStore.initialize(project, compiler)
    app = create_app(project)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8765",
            headers=ACCEPT,
        ) as client:
            assert (
                await client.post(
                    "/mcp", json=INITIALIZE, headers={"Origin": "http://127.0.0.1:8123"}
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/mcp", json=INITIALIZE, headers={"Origin": "http://127.0.0.1:8765"}
                )
            ).status_code == 200


async def test_dashboard_runs_without_optional_mcp_and_http_explains_install(
    tmp_path, compiler, monkeypatch
):
    project = tmp_path / "without-sdk.sqlite"
    DatasetStore.initialize(project, compiler)
    original = builtins.__import__

    def no_mcp(name, *args, **kwargs):
        if name.startswith("mcp."):
            raise ModuleNotFoundError("No module named mcp", name="mcp")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_mcp)
    app = create_app(project)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8765"
        ) as client:
            assert (await client.get("/")).status_code == 200
            response = await client.post("/mcp", json=INITIALIZE)
            assert response.status_code == 503 and ".[mcp]" in response.json()["detail"]


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "null",
        "http://localhost:8123/path",
        "http://user:pass@localhost:8123",
        "http://localhost:99999",
        "http://localhost:8123?x=1",
        "http://*.example",
        "file:///tmp/ui",
    ],
)
def test_allow_origin_requires_an_exact_http_origin(origin):
    from argparse import ArgumentTypeError

    with pytest.raises(ArgumentTypeError):
        browser_origin(origin)


@pytest.mark.filterwarnings(
    "ignore:Use `streamable_http_client` instead.:DeprecationWarning"
)
async def test_real_http_sdk_sse_and_stdio_share_the_dashboard(tmp_path, compiler):
    root = Path(__file__).parents[1]
    project = tmp_path / "transport.sqlite"
    DatasetStore.initialize(project, compiler)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "trajectory_remix",
            "--dataset",
            str(project),
            "--port",
            str(port),
            "--mcp-allow-origin",
            "http://127.0.0.1:8123",
            cwd=root,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(
                base_url=base, trust_env=False, timeout=5, headers=ACCEPT
            ) as client:
                for _ in range(100):
                    try:
                        (await client.get("/")).raise_for_status()
                        break
                    except httpx.HTTPError:
                        assert process.returncode is None, (
                            tmp_path / "server.log"
                        ).read_text()
                        await asyncio.sleep(0.05)
                else:
                    pytest.fail("Server did not start")

                def factory(headers=None, timeout=None, auth=None):
                    return httpx.AsyncClient(
                        headers=headers, timeout=timeout, auth=auth, trust_env=False
                    )

                async with streamablehttp_client(
                    base + "/mcp",
                    headers={"Origin": "http://127.0.0.1:8123"},
                    httpx_client_factory=factory,
                ) as (read, write, session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        assert session_id() and (await session.list_tools()).tools
                        created = await session.call_tool(
                            "create_trajectory", {"sample_id": "http-shared"}
                        )
                        assert not created.isError
                # Stdio continues to see exactly the same dataset owner and edits.
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "trajectory_remix.mcp_server", "--url", base],
                    cwd=str(root),
                )
                async with stdio_client(params) as streams, ClientSession(
                    *streams
                ) as session:
                    await session.initialize()
                    existing = await session.call_tool(
                        "get_trajectory", {"sample_id": "http-shared"}
                    )
                    assert (
                        not existing.isError
                        and existing.structuredContent["sample"]["id"] == "http-shared"
                    )
                # A GET event stream may stay open while POST requests keep working.
                headers = await initialize(client)
                async with client.stream(
                    "GET", "/mcp", headers={**headers, "Accept": "text/event-stream"}
                ) as stream:
                    assert stream.status_code == 200 and stream.headers[
                        "content-type"
                    ].startswith("text/event-stream")
                    ping = await client.post(
                        "/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 7, "method": "ping"},
                    )
                    assert ping.status_code == 200 and ping.json()["result"] == {}
                assert (await client.delete("/mcp", headers=headers)).status_code == 200
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
