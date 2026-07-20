"""FastAPI entrypoint. The graph is triggered three ways:

1. Automatically, via a background task LISTENing on Postgres' `new_anomaly` channel
   (data_pipeline/sql/005_notify_anomaly.sql) — Tower's runway-intersection conflict rule
   IS an anomaly type (PROXIMITY_CONFLICT), so this path stays live for the KSBA rescope.
2. Manually, via POST /trigger/{anomaly_id} — for testing the anomaly path without waiting
   on a real one.
3. Manually, via POST /trigger/{role}/{icao24} — invokes one of the 4 roles directly on a
   specific aircraft. This is currently the ONLY way to reach CLEARANCE: an aircraft awaiting
   an IFR clearance at the gate typically isn't broadcasting ADS-B yet, so it can't be found
   in aircraft_state_current the way a taxiing/airborne aircraft can, and observation_builder
   never buckets anything into CLEARANCE (see state.py's note). This is a structural
   limitation of live radar data, not a v1 shortcut — a later stage may add live
   phase-of-flight NOTIFY triggering for GROUND/TOWER/APPROACH, but CLEARANCE stays
   manual/synthetic regardless.
"""

import asyncio
import contextlib
import json
import logging

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

import scenarios as scenario_catalog
from config import settings
from graph import build_graph
from mcp_client import MCPToolClient, build_langchain_tools
from roles import ROLE_FACILITY_NAME, ROLE_FREQUENCY_MHZ, ROLE_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent-orchestration")

app = FastAPI(title="ATC Agent Orchestration")

_state: dict = {}


async def _run_graph(initial_state: dict) -> dict:
    graph = _state["graph"]
    # Hard safety net, not just a workaround: a reasoning loop that never terminates (model
    # keeps re-requesting a conflict check, keeps calling tools, etc.) should fail fast and
    # loud rather than run indefinitely — this is exactly the kind of runaway-cost/runaway-
    # action failure mode a deterministic cap protects against, consistent with the project's
    # own "deterministic safety guardrails" guardrail.
    return await graph.ainvoke(initial_state, config={"recursion_limit": 12})


def _base_initial_state() -> dict:
    return {
        "messages": [],
        "active_role": "IDLE",
        "aircraft_by_role": {},
        "handoff_buffer": {},
        "final_instruction": None,
        "active_anomalies": [],
    }


async def _run_graph_for_anomaly(anomaly_id: int) -> dict:
    initial_state = _base_initial_state()
    initial_state["messages"] = [HumanMessage(content=f"New anomaly detected: anomaly_events.id={anomaly_id}")]
    # observation_builder overwrites this with the live open-anomaly list, but the entry
    # router needs a hint about *which* anomaly triggered this run, so seed it.
    initial_state["active_anomalies"] = [{"id": anomaly_id, "aircraft_icao24_1": None}]

    async with _state["pool"].acquire() as conn:
        row = await conn.fetchrow(
            "SELECT aircraft_icao24_1 FROM anomaly_events WHERE id = $1", anomaly_id
        )
    if row is None:
        raise HTTPException(404, f"anomaly_events.id={anomaly_id} not found")
    initial_state["active_anomalies"][0]["aircraft_icao24_1"] = row["aircraft_icao24_1"]

    return await _run_graph(initial_state)


async def _run_graph_for_role(role: str, icao24: str, context: str | None) -> dict:
    initial_state = _base_initial_state()
    initial_state["active_role"] = role  # bypasses the anomaly-based entry router entirely
    seed_text = context or (
        f"{icao24} checking in with {role.title()}."
        if role != "CLEARANCE"
        else f"{icao24} requesting IFR clearance."
    )
    initial_state["messages"] = [HumanMessage(content=seed_text)]
    return await _run_graph(initial_state)


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

    # max_tokens is deliberately generous, not the 4096 LangChain default: Claude Sonnet 5
    # runs adaptive thinking by default even without an explicit `thinking` param, and
    # thinking + the tool-heavy system prompt + the actual response all share one budget.
    # A tight budget risks the model's real decision (text or a finalize_instruction call)
    # being silently truncated to nothing after thinking consumes most of it — observed live
    # during Phase 4 integration testing on the old 2-agent design.
    llm = ChatAnthropic(model=settings.llm_model, api_key=settings.anthropic_api_key, max_tokens=16000)

    _state["graph"] = build_graph(
        llm, mcp_tools, _state["pool"], airport_lat=settings.airport_lat, airport_lon=settings.airport_lon
    )
    _state["listener_task"] = asyncio.create_task(_listen_for_anomalies())
    log.info("agent_orchestration ready (KSBA, 4-role)")


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


def _tool_calls_trace(final_state: dict) -> list[dict]:
    """Every tool call made across this run, in order — lets the dashboard show that a
    decision was grounded in a real query_radar/query_faa_rules call (or a deterministic
    check_runway_conflict/advance_to_next_role), not just plausible-sounding text."""
    trace = []
    for message in final_state["messages"]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                trace.append({"name": call["name"], "args": call["args"]})
    return trace


async def _aircraft_position(icao24: str) -> dict | None:
    async with _state["pool"].acquire() as conn:
        row = await conn.fetchrow(
            "SELECT longitude, latitude FROM aircraft_state_current WHERE icao24 = $1", icao24
        )
    return {"longitude": row["longitude"], "latitude": row["latitude"]} if row else None


async def _final_response(final_state: dict, icao24: str | None = None) -> dict:
    return {
        "active_role": final_state["active_role"],
        "handoff_buffer": final_state["handoff_buffer"],
        "message_count": len(final_state["messages"]),
        "final_message": str(final_state["messages"][-1].content) if final_state["messages"] else None,
        "tool_calls": _tool_calls_trace(final_state),
        # None for CLEARANCE (the aircraft has no radar position yet — see module docstring).
        "aircraft_position": await _aircraft_position(icao24) if icao24 else None,
    }


@app.post("/trigger/{anomaly_id}")
async def trigger_anomaly(anomaly_id: int) -> dict:
    """Manually run the graph for an existing anomaly_events row — for testing."""
    return await _final_response(await _run_graph_for_anomaly(anomaly_id))


class TriggerRoleRequest(BaseModel):
    # Freeform context for the seed message — required in practice for CLEARANCE (the
    # aircraft has no radar state to fall back on) and useful for the other 3 roles when
    # testing a scenario query_radar alone wouldn't capture (e.g. a specific traffic
    # conflict). Optional: a generic check-in message is used if omitted.
    context: str | None = None


@app.post("/trigger/{role}/{icao24}")
async def trigger_role(role: str, icao24: str, body: TriggerRoleRequest | None = None) -> dict:
    """Manually invoke one role's reasoning on a specific aircraft — the only way to reach
    CLEARANCE (see module docstring), and the mechanism used to verify the full
    Clearance -> Ground -> Tower -> Approach chain end-to-end before live auto-triggering
    exists for GROUND/TOWER/APPROACH."""
    role_upper = role.upper()
    if role_upper not in ROLE_NAMES:
        raise HTTPException(400, f"role must be one of {list(ROLE_NAMES)}, got {role!r}")
    context = body.context if body else None
    final_state = await _run_graph_for_role(role_upper, icao24, context)
    return await _final_response(final_state, icao24=icao24 if role_upper != "CLEARANCE" else None)


@app.get("/airport/layout")
async def airport_layout() -> dict:
    """Real runway geometry + role frequencies — the dashboard's only source of truth for
    drawing the diagram, so it can't silently drift from the actual seeded PostGIS data."""
    async with _state["pool"].acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT runway_id, heading_deg,
                   ST_X(threshold_geom::geometry) AS threshold_lon,
                   ST_Y(threshold_geom::geometry) AS threshold_lat,
                   ST_X(ST_StartPoint(centerline_geom::geometry)) AS centerline_start_lon,
                   ST_Y(ST_StartPoint(centerline_geom::geometry)) AS centerline_start_lat,
                   ST_X(ST_EndPoint(centerline_geom::geometry)) AS centerline_end_lon,
                   ST_Y(ST_EndPoint(centerline_geom::geometry)) AS centerline_end_lat,
                   intersects_with
            FROM runway
            ORDER BY runway_id
            """
        )
    return {
        "airport_lat": settings.airport_lat,
        "airport_lon": settings.airport_lon,
        "runways": [dict(r) for r in rows],
        "roles": [
            {"role": role, "facility_name": ROLE_FACILITY_NAME[role], "frequency_mhz": ROLE_FREQUENCY_MHZ[role]}
            for role in ROLE_NAMES
        ],
    }


@app.get("/scenarios")
async def list_scenarios() -> dict:
    return {
        "scenarios": [
            {
                "id": scenario_id,
                "title": scenario["title"],
                "description": scenario["description"],
                "steps": [
                    {"role": s["role"], "icao24": s["icao24"], "label": s["label"], "context": s["context"]}
                    for s in scenario["steps"]
                ],
            }
            for scenario_id, scenario in scenario_catalog.SCENARIOS.items()
        ]
    }


@app.post("/scenarios/{scenario_id}/reset")
async def reset_scenario(scenario_id: str) -> dict:
    """Idempotently (re)seeds this scenario's synthetic aircraft — called by the dashboard
    immediately before playing back a scenario's chain of /trigger calls."""
    if scenario_id not in scenario_catalog.SCENARIOS:
        raise HTTPException(404, f"Unknown scenario_id={scenario_id!r}")
    await scenario_catalog.seed_scenario(_state["pool"], scenario_id)
    return {"status": "seeded", "scenario_id": scenario_id}


# Mounted last so it doesn't shadow the API routes above. dashboard/ is bind-mounted into
# the container at /app/dashboard (WORKDIR is /app — see Dockerfile) for dev convenience,
# same rationale as mcp_server's vector_db bind mount: edit dashboard files without a rebuild.
app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")
