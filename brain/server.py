"""Local, offline-friendly MCP server over stdio: the same tools as the hosted /mcp endpoint.

    python -m brain.server

Everything works without a database except `sql`, `freshness` and the live part of `catalog`; those read the connection URL from env ANALYTICS_READER_URL or from the file named by
LONVERA_ANALYTICS_URL_FILE (default ~/lonvera-tag-explorer-data/analytics_reader_url.txt). The URL is never printed.
"""
from __future__ import annotations

import asyncio

from . import tools as T
from .instructions import INSTRUCTIONS as INSTR

INSTRUCTIONS = INSTR


def build_server(brain: T.Brain | None = None):
    from mcp import types
    from mcp.server.lowlevel import Server

    brain = brain or T.Brain(allow_file_url=True)
    server = Server("lonvera-brain", instructions=INSTRUCTIONS)

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [types.Tool(name=t.name, description=t.description, inputSchema=t.schema) for t in T.TOOLS]

    @server.call_tool(validate_input=False)
    async def _call(name: str, arguments: dict | None):
        try:
            result = await asyncio.to_thread(T.call, brain, name, arguments)
        except T.ToolError as e:
            raise ValueError(str(e)) from None
        return [types.TextContent(type="text", text=T.dumps(result))]

    return server


async def _main() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
