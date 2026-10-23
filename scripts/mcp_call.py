"""Call the running dashboard through the official MCP stdio client.

Read {name, arguments} or an array of calls from stdin; output structured JSON.
This also serves as an external-worker adapter without provider credentials.
"""

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def connect(url):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "trajectory_remix.mcp_server", "--url", url],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        yield session


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    data = result.structuredContent or {
        "content": [c.model_dump() for c in result.content]
    }
    if result.isError:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    requests = json.loads(args.input.read_text() if args.input else sys.stdin.read())
    multiple = isinstance(requests, list)
    async with connect(args.url) as session:
        results = [
            await call(session, r["name"], r.get("arguments", {}))
            for r in (requests if multiple else [requests])
        ]
    print(json.dumps(results if multiple else results[0], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
