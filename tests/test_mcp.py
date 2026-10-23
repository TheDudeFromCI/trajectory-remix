from trajectory_remix.storage import DatasetStore
import asyncio
import json
import socket
import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp", reason="Install .[mcp,test] to run MCP protocol tests")

import anyio
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_client_server_memory_streams

from trajectory_remix.mcp_server import DashboardBridge, create_mcp_server, local_url
from trajectory_remix.server import create_app


async def test_mcp_protocol_discovery_structured_results_and_errors(compiler, tmp_path):
    DatasetStore.initialize(tmp_path / "project.kidra-dataset", compiler)
    app = create_app(tmp_path / "project.kidra-dataset")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        bridge = DashboardBridge(client)
        server = create_mcp_server(bridge)
        async with (
            create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ),
            anyio.create_task_group() as tasks,
        ):
            tasks.start_soon(
                server.run, *server_streams, server.create_initialization_options()
            )
            async with ClientSession(*client_streams) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "trajectory-remix"
                catalog = await session.list_tools()
                tools = {tool.name: tool for tool in catalog.tools}
                assert len(tools) == 39
                assert tools["get_trajectory"].annotations.readOnlyHint
                assert tools["delete_event"].annotations.destructiveHint
                for tool in tools.values():
                    Draft202012Validator.check_schema(tool.inputSchema)
                    assert tool.inputSchema["additionalProperties"] is False
                assert "validate" in tools["create_event"].inputSchema["properties"]
                assert (
                    "validate_" not in tools["create_event"].inputSchema["properties"]
                )
                created = await session.call_tool(
                    "create_trajectory",
                    {"sample_id": "from-mcp", "template": "demo"},
                )
                assert not created.isError
                assert created.structuredContent["sample_id"] == "from-mcp"
                assert json.loads(created.content[0].text) == created.structuredContent
                result = await session.call_tool(
                    "get_trajectory", {"sample_id": "from-mcp"}
                )
                assert result.structuredContent["sample"]["replay"]
                report = await session.call_tool(
                    "validate_trajectory", {"sample_id": "from-mcp"}
                )
                assert report.structuredContent["valid"]
                duplicate = await session.call_tool(
                    "create_trajectory", {"sample_id": "from-mcp"}
                )
                assert (
                    duplicate.isError
                    and duplicate.structuredContent["code"] == "conflict"
                )
                invalid = await session.call_tool("list_trajectories", {"limit": True})
                assert (
                    invalid.isError
                    and invalid.structuredContent["code"] == "invalid_arguments"
                )
                missing = await session.call_tool("does_not_exist", {})
                assert missing.isError
                batched = await session.call_tool(
                    "apply_batch",
                    {
                        "edits": [
                            {
                                "name": "update_trajectory_metadata",
                                "arguments": {
                                    "sample_id": "from-mcp",
                                    "patch": {"metadata": {"title": "batched"}},
                                },
                            }
                        ]
                    },
                )
                assert (
                    not batched.isError
                    and len(batched.structuredContent["results"]) == 1
                )
                assert not (await session.call_tool("undo_edits", {})).isError
                assert not (await session.call_tool("redo_edits", {})).isError
                assert (
                    await session.call_tool(
                        "diff_trajectories",
                        {
                            "sample_id": "from-mcp",
                            "other_sample_id": "from-mcp",
                        },
                    )
                ).structuredContent["equal"]
                assert not (
                    await session.call_tool(
                        "create_tool_session", {"session_id": "tools"}
                    )
                ).isError
                dispatched = await session.call_tool(
                    "call_simulated_tool",
                    {
                        "session_id": "tools",
                        "name": "file_write",
                        "arguments": {"path": "/report", "content": "actual"},
                        "record_sample_id": "from-mcp",
                    },
                )
                assert (
                    not dispatched.isError and dispatched.structuredContent["recording"]
                )
                state = await session.call_tool(
                    "get_tool_session", {"session_id": "tools"}
                )
                assert state.structuredContent["state"]["files"]["/report"] == "actual"
            tasks.cancel_scope.cancel()


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:8765",
        "http://example.com",
        "http://127.0.0.1.evil.example",
        "http://127.0.0.1@evil.example",
        "http://user:pass@localhost:8765",
        "http://localhost:8765/api",
        "http://localhost:8765?token=secret",
        "http://localhost:8765/#fragment",
        "http://localhost:99999",
        "http://localhost:0",
        "file:///tmp/project.kidra-dataset",
        "http://[bad",
    ],
)
def test_bridge_rejects_nonlocal_or_ambiguous_urls(value):
    from argparse import ArgumentTypeError

    with pytest.raises(ArgumentTypeError):
        local_url(value)


@pytest.mark.parametrize(
    "value", ["http://127.0.0.1:8765/", "http://localhost:8766", "http://[::1]:8765"]
)
def test_bridge_accepts_loopback_urls(value):
    assert local_url(value) == value.rstrip("/")


async def test_bridge_refreshes_session_token_after_dashboard_restart():
    calls = []
    bootstrap_count = 0

    def response(request):
        nonlocal bootstrap_count
        calls.append(request)
        if request.url.path == "/":
            bootstrap_count += 1
            return httpx.Response(
                200,
                text=f'<meta name="remix-token" content="session-{bootstrap_count}">',
            )
        if request.headers["X-Remix-Token"] != "session-2":
            return httpx.Response(403, json={"detail": "reload"})
        return httpx.Response(200, json={"sample_id": "created-once"})

    async with httpx.AsyncClient(
        base_url="http://localhost:8765", transport=httpx.MockTransport(response)
    ) as client:
        failed, result = await DashboardBridge(client).call(
            "create_trajectory", {"sample_id": "created-once"}
        )
    assert not failed and result["sample_id"] == "created-once"
    assert bootstrap_count == 2
    assert [r.url.path for r in calls] == ["/", "/api/mcp/call", "/", "/api/mcp/call"]


async def test_bridge_does_not_retry_ambiguous_mutation_failures():
    attempts = []

    def response(request):
        if request.url.path == "/":
            return httpx.Response(
                200, text='<meta name="remix-token" content="test-token">'
            )
        attempts.append(request)
        raise httpx.ReadTimeout("Connection lost after sending", request=request)

    async with httpx.AsyncClient(
        base_url="http://localhost:8765", transport=httpx.MockTransport(response)
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await DashboardBridge(client).call("create_trajectory", {})
    assert len(attempts) == 1


async def test_real_stdio_client_shares_dashboard_and_persists(tmp_path, compiler):
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    dataset = tmp_path / "stdio.kidra-dataset"
    DatasetStore.initialize(dataset, compiler)
    with (tmp_path / "dashboard.log").open("w") as log:
        dashboard = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "trajectory_remix",
            "--dataset",
            str(dataset),
            "--port",
            str(port),
            cwd=root,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(
                base_url=base, trust_env=False, timeout=1
            ) as client:
                for _ in range(100):
                    try:
                        response = await client.get("/")
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        assert dashboard.returncode is None, (
                            tmp_path / "dashboard.log"
                        ).read_text()
                        await asyncio.sleep(0.05)
                else:
                    pytest.fail("Dashboard did not start")
                params = StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-m",
                        "trajectory_remix.mcp_server",
                        "--url",
                        base,
                    ],
                    cwd=str(root),
                )
                async with (
                    stdio_client(params) as streams,
                    ClientSession(*streams) as session,
                ):
                    await session.initialize()
                    assert (await session.list_tools()).tools
                    result = await session.call_tool(
                        "create_trajectory",
                        {"sample_id": "stdio-test", "template": "demo"},
                    )
                    assert not result.isError
                    bridge = DashboardBridge(client)
                    response = await bridge.request("GET", "/api/samples/0")
                    assert response.json()["id"] == "stdio-test"
                    scheduled = await session.call_tool(
                        "create_tool_call",
                        {
                            "sample_id": "stdio-test",
                            "tick": "150",
                            "tool": "file_write",
                            "arguments": {"path": "mcp.txt", "content": "saved"},
                            "duration_ticks": 50,
                        },
                    )
                    assert not scheduled.isError, scheduled
                    old = await session.call_tool(
                        "get_sandbox_state", {"sample_id": "stdio-test", "tick": scheduled.structuredContent["tool_call"]["start"]}
                    )
                    assert old.structuredContent["sandbox"]["files"] == {}
                    assert (
                        old.structuredContent["sandbox"]["tool_calls"][-1]["status"]
                        == "running"
                    )
                    current = await session.call_tool(
                        "get_sandbox_state", {"sample_id": "stdio-test", "tick": scheduled.structuredContent["tool_call"]["end"]}
                    )
                    assert current.structuredContent["sandbox"]["files"] == {
                        "/mcp.txt": "saved"
                    }
                    assert (
                        await session.call_tool("save_dataset", {})
                    ).structuredContent["saved"]
                assert (
                    DatasetStore(dataset).samples[0]["id"] == "stdio-test"
                )
                assert (
                    DatasetStore(dataset).samples[0]["replay"]["150"][0][
                        "op"
                    ]
                    == "tool-call"
                )
        finally:
            dashboard.terminate()
            await asyncio.wait_for(dashboard.wait(), timeout=5)
