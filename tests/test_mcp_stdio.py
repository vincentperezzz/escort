import asyncio
import json
import sys

from taglish_listener.paths import repo_root


def test_stdio_mcp_client_reads_cards():
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    server = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "taglish_listener",
            "serve",
            "--transport",
            "stdio",
            "--replay",
            "--transcriber",
            "scripted",
            "--spark",
            "rules",
            "--segmenter",
            "fallback",
        ],
        cwd=str(repo_root()),
        env={"PYTHONPATH": str(repo_root())},
    )

    async def run():
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {"get_live_context", "get_latest_frame", "get_cards", "push_suggestion"} <= names
                result = await session.call_tool("get_cards", {"limit": 20})
                payload = json.loads(result.content[0].text)
                assert {card["type"] for card in payload["cards"]} == {
                    "PRD_CONFLICT",
                    "COMMITMENT",
                    "SUGGESTED_REPLY",
                }
                frame = await session.call_tool("get_latest_frame", {})
                assert frame.content[0].type == "image"

    asyncio.run(run())
