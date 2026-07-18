"""MCP protocol server exposing query_radar and query_faa_rules over stdio.

Tool binding over hardcoding, per README.md's guardrails — an LLM agent calls these as
declared MCP tools rather than the orchestration layer gluing together raw SQL/vector-search
calls itself.
"""

import asyncio
import json
import logging

import mcp.server.stdio
import mcp.types as types
import psycopg2
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from config import settings
from tools.query_faa_rules import FaaRulesIndex, query_faa_rules
from tools.query_radar import query_radar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mcp-server")

server = Server("atc-mcp-server")

# Both the FAISS index and the Postgres connection are loaded once at process startup and
# reused across calls — re-embedding the model or reconnecting per call would make every
# tool call needlessly slow.
_faa_index: FaaRulesIndex | None = None
_pg_conn: psycopg2.extensions.connection | None = None


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="query_radar",
            description=(
                "Get current position, wake category, open safety anomalies, and recent "
                "track for one commercial IFR aircraft by ICAO24 hex address."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "flight_id": {
                        "type": "string",
                        "description": "ICAO24 hex address (aircraft_state_current.icao24)",
                    }
                },
                "required": ["flight_id"],
            },
        ),
        types.Tool(
            name="query_faa_rules",
            description=(
                "Vector search over FAA Order JO 7110.65 (wake turbulence separation, NTZ "
                "breakout procedures, same-runway departure intervals) and JO 7360.1 "
                "(wake turbulence category definitions). Accepts multiple topics per call; "
                "each topic is searched independently and results are deduplicated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "e.g. ['RECAT category B leading category E separation', "
                        "'NTZ breakout lateral divergence minimums']",
                    }
                },
                "required": ["topics"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "query_radar":
        result = query_radar(arguments["flight_id"], _pg_conn)
    elif name == "query_faa_rules":
        result = query_faa_rules(arguments["topics"], _faa_index)
    else:
        raise ValueError(f"Unknown tool: {name}")

    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


async def main() -> None:
    global _faa_index, _pg_conn

    log.info("Loading FAISS index...")
    _faa_index = FaaRulesIndex()

    log.info("Connecting to PostGIS...")
    _pg_conn = psycopg2.connect(settings.postgres_dsn)

    log.info("Starting MCP stdio server")
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="atc-mcp-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
