"""FastAPI entrypoint. The graph is triggered two ways:

1. Automatically, via a background task LISTENing on Postgres' `new_anomaly` channel
   (data_pipeline/sql/005_notify_anomaly.sql) — the primary trigger path in production.
2. Manually, via POST /trigger/{anomaly_id} — for testing without waiting on a real anomaly.
"""

import asyncio
import contextlib
import json
import logging

import asyncpg
from fastapi import FastAPI, HTTPException
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from config import settings
from graph import build_graph
from mcp_client import MCPToolClient, build_langchain_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent-orchestration")

app = FastAPI(title="ATC Agent Orchestration")

_state: dict = {}


async def _run_graph_for_anomaly(anomaly_id: int) -> dict:
    graph = _state["graph"]
    initial_state = {
        "messages": [HumanMessage(content=f"New anomaly detected: anomaly_events.id={anomaly_id}")],
        "active_agent": "COORDINATOR",
        "north_aircraft": [],
        "south_aircraft": [],
        "inter_agent_buffer": {},
        # observation_builder overwrites this with the live open-anomaly list, but the
        # entry router needs a hint about *which* anomaly triggered this run, so seed it.
        "active_anomalies": [{"id": anomaly_id, "aircraft_icao24_1": None}],
    }

    async with _state["pool"].acquire() as conn:
        row = await conn.fetchrow(
            "SELECT aircraft_icao24_1 FROM anomaly_events WHERE id = $1", anomaly_id
        )
    if row is None:
        raise HTTPException(404, f"anomaly_events.id={anomaly_id} not found")
    initial_state["active_anomalies"][0]["aircraft_icao24_1"] = row["aircraft_icao24_1"]

    final_state = await graph.ainvoke(initial_state)
    return final_state


async def _listen_for_anomalies() -> None:
    """Background task: dedicated LISTEN connection (LISTEN/NOTIFY requires holding a
    connection open, so this can't share the pool used for ordinary queries)."""
    conn = await asyncpg.connect(settings.postgres_dsn)

    def _on_notify(_conn, _pid, _channel, payload: str) -> None:
        data = json.loads(payload)
        log.info("Received new_anomaly notification: %s", data)
        asyncio.create_task(_handle_notification(data["id"]))

    await conn.add_listener("new_anomaly", _on_notify)
    log.info("Listening for new_anomaly notifications...")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await conn.remove_listener("new_anomaly", _on_notify)
        await conn.close()


async def _handle_notification(anomaly_id: int) -> None:
    try:
        await _run_graph_for_anomaly(anomaly_id)
    except Exception:
        log.exception("Graph run failed for anomaly_id=%s", anomaly_id)


@app.on_event("startup")
async def startup() -> None:
    _state["pool"] = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    mcp_client = MCPToolClient(settings.mcp_server_url)
    await mcp_client.connect()
    _state["mcp_client"] = mcp_client
    mcp_tools = build_langchain_tools(mcp_client)

    llm = ChatAnthropic(model=settings.llm_model, api_key=settings.anthropic_api_key)

    _state["graph"] = build_graph(llm, mcp_tools, _state["pool"])
    _state["listener_task"] = asyncio.create_task(_listen_for_anomalies())
    log.info("agent_orchestration ready")


@app.on_event("shutdown")
async def shutdown() -> None:
    _state["listener_task"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _state["listener_task"]
    await _state["mcp_client"].aclose()
    await _state["pool"].close()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/trigger/{anomaly_id}")
async def trigger(anomaly_id: int) -> dict:
    """Manually run the graph for an existing anomaly_events row — for testing."""
    final_state = await _run_graph_for_anomaly(anomaly_id)
    return {
        "active_agent": final_state["active_agent"],
        "inter_agent_buffer": final_state["inter_agent_buffer"],
        "message_count": len(final_state["messages"]),
        "final_message": str(final_state["messages"][-1].content) if final_state["messages"] else None,
    }
