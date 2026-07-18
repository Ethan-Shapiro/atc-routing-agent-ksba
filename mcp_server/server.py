"""MCP protocol server exposing query_radar and query_faa_rules over SSE.

Tool binding over hardcoding, per README.md's guardrails — an LLM agent calls these as
declared MCP tools rather than the orchestration layer gluing together raw SQL/vector-search
calls itself.

SSE (not stdio) is deliberate: mcp_server and agent_orchestration are separate Docker
containers in a microservices architecture. Stdio transport only works when a client spawns
the server as a subprocess in the same process tree — it can't cross a container boundary.
SSE lets mcp_server run as its own long-lived service that agent_orchestration (or anything
else on the atc_net network) connects to over HTTP.
"""

import json
import logging
from contextlib import asynccontextmanager

import mcp.types as types
import psycopg2
import uvicorn
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route

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


sse_transport = SseServerTransport("/messages/")


async def handle_sse(request):
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (
        read_stream,
        write_stream,
    ):
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


async def healthcheck(request):
    from starlette.responses import PlainTextResponse

    return PlainTextResponse("ok")


@asynccontextmanager
async def lifespan(app: Starlette):
    global _faa_index, _pg_conn
    log.info("Loading FAISS index...")
    _faa_index = FaaRulesIndex()
    log.info("Connecting to PostGIS...")
    _pg_conn = psycopg2.connect(settings.postgres_dsn)
    log.info("mcp_server ready on /sse")
    yield


app = Starlette(
    routes=[
        Route("/health", endpoint=healthcheck),
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
