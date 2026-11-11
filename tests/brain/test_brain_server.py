"""The local stdio server lists the same tools and answers a call, in a real subprocess (skipped when the mcp package is not installed: it is in requirements-dev.txt only)."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters                      # noqa: E402
from mcp.client.stdio import stdio_client                                  # noqa: E402

from brain import tools as T                                              # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


async def _session_run(fn):
    env = {k: v for k, v in os.environ.items() if k not in ("ANALYTICS_READER_URL", "DATABASE_URL")}
    env.update(PYTHONPATH=str(ROOT), LONVERA_ANALYTICS_URL_FILE="/nonexistent/url.txt")
    params = StdioServerParameters(command=sys.executable, args=["-m", "brain.server"], cwd=str(ROOT), env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def test_the_server_lists_its_tools_and_answers_offline_calls():
    async def go(s):
        tools = await s.list_tools()
        cat = await s.call_tool("catalog", {})
        sql = await s.call_tool("sql", {"query": "select 1"})
        return tools, cat, sql

    tools, cat, sql = asyncio.run(asyncio.wait_for(_session_run(go), 60))
    assert [t.name for t in tools.tools] == [t.name for t in T.TOOLS]
    assert all(t.description and t.inputSchema["type"] == "object" for t in tools.tools)
    assert not cat.isError and json.loads(cat.content[0].text)["domains"]
    assert sql.isError and "SQL is not configured" in sql.content[0].text
