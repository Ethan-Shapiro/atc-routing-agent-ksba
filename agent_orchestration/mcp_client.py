"""Persistent MCP client connection to mcp_server (SSE transport), exposed as LangChain
tools so the reasoning nodes bind to them the normal LangGraph way (tool binding over
hardcoding — the graph never calls query_radar/query_faa_rules directly, only through the
declared MCP tool contract, same as a real external MCP server would be consumed).
"""

import json
from contextlib import AsyncExitStack

from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.sse import sse_client
from pydantic import BaseModel, Field


class MCPToolClient:
    """Holds one long-lived SSE connection + MCP session for the life of the service,
    rather than reconnecting per tool call."""

    def __init__(self, server_url: str):
        self._server_url = server_url
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        read_stream, write_stream = await self._stack.enter_async_context(
            sse_client(self._server_url)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()

    async def aclose(self) -> None:
        await self._stack.aclose()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        assert self._session is not None, "MCPToolClient.connect() not called yet"
        result = await self._session.call_tool(name, arguments)
        text = result.content[0].text
        return json.loads(text)


class QueryRadarInput(BaseModel):
    flight_id: str = Field(description="ICAO24 hex address (aircraft_state_current.icao24)")


class QueryFaaRulesInput(BaseModel):
    topics: list[str] = Field(
        description="e.g. ['RECAT category B leading category E separation', "
        "'NTZ breakout lateral divergence minimums']"
    )


def build_langchain_tools(mcp_client: MCPToolClient) -> list[StructuredTool]:
    async def _query_radar(flight_id: str) -> dict:
        return await mcp_client.call_tool("query_radar", {"flight_id": flight_id})

    async def _query_faa_rules(topics: list[str]) -> dict:
        return await mcp_client.call_tool("query_faa_rules", {"topics": topics})

    return [
        StructuredTool.from_function(
            name="query_radar",
            description=(
                "Get current position, wake category, open safety anomalies, and recent "
                "track for one commercial IFR aircraft by ICAO24 hex address."
            ),
            args_schema=QueryRadarInput,
            coroutine=_query_radar,
        ),
        StructuredTool.from_function(
            name="query_faa_rules",
            description=(
                "Vector search over FAA Order JO 7110.65 (wake turbulence separation, NTZ "
                "breakout procedures, same-runway departure intervals) and JO 7360.1 "
                "(wake turbulence category definitions). Accepts multiple topics per call."
            ),
            args_schema=QueryFaaRulesInput,
            coroutine=_query_faa_rules,
        ),
    ]
