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
from datetime import datetime, timedelta, timezone

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
from replay import ReplayController
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
    # The seed text is free-text radio phraseology (a callsign, not an ICAO24), so without
    # this the model has no legitimate way to know which aircraft_state_current row a given
    # callsign maps to — observed live: it either grabbed an unrelated aircraft that happened
    # to be in its jurisdiction list, or fabricated a plausible-looking hex code outright.
    # icao24 is the one piece of ground truth this endpoint already has (from the URL), so
    # hand it over explicitly rather than making the model guess.
    seed_text = f"[Aircraft ICAO24: {icao24}] {seed_text}"
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
    _state["pool"] = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=8)

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
    _state["replay"] = ReplayController(
        _state["pool"], settings.postgres_dsn, settings.airport_lat, settings.airport_lon, _on_replay_event
    )
    log.info("agent_orchestration ready (KSBA, 4-role)")


@app.on_event("shutdown")
async def shutdown() -> None:
    await _state["replay"].stop()
    _state["listener_task"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _state["listener_task"]
    await _state["mcp_client"].aclose()
    await _state["pool"].close()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _message_text(content) -> str | None:
    """AIMessage.content is a plain string only when the model skipped extended thinking.
    Claude Sonnet 5 runs adaptive thinking by default, so a turn that doesn't call
    finalize_instruction (and therefore never reaches phraseology_generator's own clean
    AIMessage) ends with the reasoning node's raw response instead — content is then a list
    of {"type": "thinking"/"text", ...} blocks, and str()-ing that list directly produces an
    unreadable Python repr of the whole structure, thinking block included."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in text_parts if p) or None
    return str(content)


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
        "final_message": _message_text(final_state["messages"][-1].content) if final_state["messages"] else None,
        "tool_calls": _tool_calls_trace(final_state),
        # None for CLEARANCE (the aircraft has no radar position yet — see module docstring).
        "aircraft_position": await _aircraft_position(icao24) if icao24 else None,
    }


@app.post("/trigger/{anomaly_id}")
async def trigger_anomaly(anomaly_id: int) -> dict:
    """Manually run the graph for an existing anomaly_events row — for testing."""
    return await _final_response(await _run_graph_for_anomaly(anomaly_id))


class PositionUpdate(BaseModel):
    longitude: float
    latitude: float
    baro_altitude_m: float
    on_ground: bool
    velocity_mps: float


class TriggerRoleRequest(BaseModel):
    # Freeform context for the seed message — required in practice for CLEARANCE (the
    # aircraft has no radar state to fall back on) and useful for the other 3 roles when
    # testing a scenario query_radar alone wouldn't capture (e.g. a specific traffic
    # conflict). Optional: a generic check-in message is used if omitted.
    context: str | None = None
    # Optional: move the aircraft to match this step's point in a scenario's narrative
    # before running the graph. Without this, a multi-step scenario's aircraft stays frozen
    # at wherever it was originally seeded — observed live: a "clear of the runway, taxiing
    # in" step still showed the aircraft airborne 9 NM out, and the model (correctly)
    # refused to issue taxi instructions rather than act on the stale position.
    position: PositionUpdate | None = None


_UPDATE_AIRCRAFT_POSITION_SQL = """
UPDATE aircraft_state_current
SET longitude = $1, latitude = $2, baro_altitude_m = $3, on_ground = $4, velocity_mps = $5,
    last_contact = now()
WHERE icao24 = $6
"""


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

    if body and body.position:
        p = body.position
        async with _state["pool"].acquire() as conn:
            await conn.execute(
                _UPDATE_AIRCRAFT_POSITION_SQL,
                p.longitude, p.latitude, p.baro_altitude_m, p.on_ground, p.velocity_mps, icao24,
            )

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
                # Lets the dashboard animate the very first leg of a flight path — without
                # this it has no "from" position until the first step's response arrives.
                "seed_positions": {
                    a["icao24"]: {"longitude": a["longitude"], "latitude": a["latitude"]}
                    for a in scenario["seed_aircraft"]
                },
                "steps": [
                    {
                        "role": s["role"],
                        "icao24": s["icao24"],
                        "label": s["label"],
                        "context": s["context"],
                        "position": s.get("position"),
                    }
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


# ----------------------------------------------------------------------------------------
# Recorded-day replay (see replay.py). The dashboard's "Recorded Day" mode drives these.
# ----------------------------------------------------------------------------------------

# Caps how many uncached replay agent runs execute at once. The first pass through a window
# can surface ~10 role transitions near-simultaneously; without a cap they'd all fire their
# LLM calls together, hammer the pool + the API, and (before the dedicated replay connection)
# starve the tick loop. 3 keeps the first pass brisk without a thundering herd.
_replay_agent_semaphore = asyncio.Semaphore(3)


def _replay_fingerprint(kind: str, icao24: str, role: str, replay_time: datetime) -> str:
    """Content-derived, stable across loops: the same event recurs at the same replay-clock
    instant every loop, so bucketing to 10s gives a key that hits the cache on loop 2+."""
    bucket = int(replay_time.timestamp() // 10)
    return f"{kind}:{icao24}:{role}@{bucket}"


async def _on_replay_event(
    kind: str, icao24: str, callsign: str | None, role: str, replay_time: datetime, prev_role: str | None
) -> None:
    """Called by the replay driver on a role transition (fire-and-forget). Cache-first: if
    this exact event was already resolved on an earlier loop, reuse it and make no API call —
    the mechanism that makes re-looping free."""
    replay = _state["replay"]
    session_id = replay.status()["session_id"]
    if session_id is None:
        return
    fingerprint = _replay_fingerprint(kind, icao24, role, replay_time)

    cached = await _state["pool"].fetchval(
        "SELECT 1 FROM replay_agent_response WHERE session_id = $1 AND event_fingerprint = $2",
        session_id, fingerprint,
    )
    if cached:
        return  # already decided on a previous loop — no re-ping

    context = (
        f"{callsign or icao24} is now under your control ({role} phase"
        + (f", handed off from {prev_role}" if prev_role else "") + ")."
    )
    try:
        async with _replay_agent_semaphore:
            final_state = await _run_graph_for_role(role, icao24, context)
    except Exception:
        log.exception("Replay agent run failed for icao24=%s role=%s", icao24, role)
        return

    final_message = _message_text(final_state["messages"][-1].content) if final_state["messages"] else None
    tool_calls = _tool_calls_trace(final_state)
    await _state["pool"].execute(
        """
        INSERT INTO replay_agent_response
            (session_id, icao24, callsign, role, trigger_kind, event_fingerprint,
             replay_time, final_message, tool_calls)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        ON CONFLICT (session_id, event_fingerprint) DO NOTHING
        """,
        session_id, icao24, callsign, role, kind, fingerprint,
        replay_time, final_message, json.dumps(tool_calls),
    )


@app.get("/replay/sessions")
async def replay_sessions() -> dict:
    rows = await _state["pool"].fetch(
        """
        SELECT s.id, s.label, s.started_at, s.ended_at,
               (SELECT count(DISTINCT h.icao24) FROM aircraft_state_history h
                WHERE h.time_position >= s.started_at AND h.time_position < s.ended_at
                  AND h.is_commercial_ifr) AS ifr_aircraft
        FROM recording_session s ORDER BY s.id DESC
        """
    )
    return {"sessions": [dict(r) for r in rows]}


class CreateSessionRequest(BaseModel):
    label: str
    last_minutes: float = 30.0


@app.post("/replay/sessions")
async def create_replay_session(body: CreateSessionRequest) -> dict:
    """Convenience for the dashboard: label the most recent `last_minutes` of recorded
    history as a session (the same thing data_pipeline/replay/record_session.py does)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=body.last_minutes)
    row = await _state["pool"].fetchrow(
        "INSERT INTO recording_session (label, started_at, ended_at) VALUES ($1, $2, $3) RETURNING id",
        body.label, start, end,
    )
    return {"id": row["id"], "label": body.label, "started_at": start.isoformat(), "ended_at": end.isoformat()}


class StartReplayRequest(BaseModel):
    speed: float = 60.0
    loop: bool = True


@app.post("/replay/{session_id}/start")
async def start_replay(session_id: int, body: StartReplayRequest | None = None) -> dict:
    body = body or StartReplayRequest()
    try:
        return await _state["replay"].start(session_id, speed=body.speed, loop=body.loop)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/replay/stop")
async def stop_replay() -> dict:
    await _state["replay"].stop()
    return _state["replay"].status()


@app.get("/replay/status")
async def replay_status() -> dict:
    return _state["replay"].status()


@app.get("/replay/{session_id}/tracks")
async def replay_tracks(session_id: int) -> dict:
    """Full per-aircraft track set for the window — fetched once so the dashboard can
    interpolate smooth motion locally against the replay clock, no per-frame round-trips."""
    session = await _state["pool"].fetchrow(
        "SELECT started_at, ended_at FROM recording_session WHERE id = $1", session_id
    )
    if session is None:
        raise HTTPException(404, f"session {session_id} not found")
    rows = await _state["pool"].fetch(
        """
        SELECT icao24, callsign, time_position, longitude, latitude,
               on_ground, true_track_deg, baro_altitude_m
        FROM aircraft_state_history
        WHERE time_position >= $1 AND time_position < $2 AND is_commercial_ifr
        ORDER BY icao24, time_position
        """,
        session["started_at"], session["ended_at"],
    )
    tracks: dict[str, dict] = {}
    for r in rows:
        t = tracks.setdefault(r["icao24"], {"icao24": r["icao24"], "callsign": r["callsign"], "points": []})
        t["points"].append({
            "t": r["time_position"].isoformat(),
            "lon": r["longitude"], "lat": r["latitude"],
            "on_ground": r["on_ground"], "track": r["true_track_deg"],
        })
    return {
        "session_id": session_id,
        "started_at": session["started_at"].isoformat(),
        "ended_at": session["ended_at"].isoformat(),
        "tracks": list(tracks.values()),
    }


@app.get("/replay/{session_id}/transmissions")
async def replay_transmissions(session_id: int) -> dict:
    """Time-ordered agent transmissions produced for this session so far. Stage 2 will merge
    real controller transmissions into the same feed for side-by-side comparison."""
    rows = await _state["pool"].fetch(
        """
        SELECT icao24, callsign, role, trigger_kind, replay_time, final_message, tool_calls
        FROM replay_agent_response
        WHERE session_id = $1 AND final_message IS NOT NULL
        ORDER BY replay_time
        """,
        session_id,
    )
    return {
        "transmissions": [
            {
                "source": "AGENT",
                "icao24": r["icao24"],
                "callsign": r["callsign"],
                "role": r["role"],
                "trigger_kind": r["trigger_kind"],
                "replay_time": r["replay_time"].isoformat(),
                "text": r["final_message"],
                "tool_calls": json.loads(r["tool_calls"]) if isinstance(r["tool_calls"], str) else r["tool_calls"],
            }
            for r in rows
        ]
    }


# Mounted last so it doesn't shadow the API routes above. dashboard/ is bind-mounted into
# the container at /app/dashboard (WORKDIR is /app — see Dockerfile) for dev convenience,
# same rationale as mcp_server's vector_db bind mount: edit dashboard files without a rebuild.
app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")
