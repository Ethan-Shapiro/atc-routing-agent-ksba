"""Offline reward scoring, per multi-agent_orchestration_rf_layer.md section 4:

    R_t = w_s*R_safety + w_t*R_throughput + w_e*R_efficiency + w_c*R_coordination

This is a replay/scoring script over recorded history, not live RL training (out of scope
per the project's own guardrails — README.md explicitly says not to build an ML anomaly
detector, and there's no policy being trained here, only past behavior being graded).

Weights below (W_*, ALPHA, BETA, GAMMA_*, C_*, LAMBDA_*) are illustrative defaults chosen to
satisfy the spec's ordering constraint (w_s >> w_t > w_e >= w_c) — they are NOT calibrated
against real training runs, since there is no training loop here to calibrate them against.
Treat them as a starting point for whoever eventually builds Phase 4/RL training, not as
tuned values.

Two reward terms are honest zeros rather than fabricated numbers, each documented at its
computation site below:
  - P_wake (safety): needs an aircraft-type -> RECAT-category lookup that Phase 2
    deliberately did not build (see mcp_server/vector_db/ingest.py's docstring on why the
    JO 7360.1K appendix tables aren't reliably extractable). wake_category is NULL for every
    aircraft in the current schema.
  - D_optimal (efficiency) and N_deadlocks (coordination): both need data this project
    doesn't ingest — a flight-plan destination fix, and a log of individual ATC instructions
    (not just their outcomes) respectively.
"""

import math
from datetime import datetime, timedelta

import asyncpg

# --- Weights (see module docstring: illustrative, not calibrated) ---
W_SAFETY = 100.0
W_THROUGHPUT = 5.0
W_EFFICIENCY = 1.0
W_COORDINATION = 1.0

ALPHA = 2.0  # lateral exponential-decay scale, per NM
BETA = 0.005  # vertical exponential-decay scale, per ft

GAMMA_LANDED = 10.0
GAMMA_DEPARTED = 10.0
GAMMA_DELAY = 0.1  # per second at standstill

C_HEADING = 0.01  # per degree of heading change
C_PATH = 0.001  # per meter of path inefficiency (unused — see D_optimal note above)

LAMBDA_UNCOORDINATED = 50.0
LAMBDA_DEADLOCK = 100.0  # unused — see N_deadlocks note above

NM_TO_M = 1852.0
STANDSTILL_VELOCITY_MPS = 2.0


async def compute_safety_reward(conn: asyncpg.Connection, start: datetime, end: datetime) -> dict:
    """R_safety, scored from anomaly_events rather than a continuous per-timestep pairwise
    scan of aircraft_state_history. anomaly_events records the exact separation AT THE
    MOMENT Phase 1's trigger fired (a real, recorded value) and how long the anomaly stayed
    open — this sums one exponential-decay penalty per anomaly, weighted by minutes open, as
    a discrete approximation of the spec's continuous sum. It does not interpolate or
    fabricate intermediate separation values the telemetry doesn't have.
    """
    rows = await conn.fetch(
        """
        SELECT lateral_distance_nm, vertical_distance_ft, detected_at,
               COALESCE(resolved_at, $2) AS effective_resolved_at
        FROM anomaly_events
        WHERE anomaly_type = 'PROXIMITY_CONFLICT'
          AND detected_at >= $1 AND detected_at < $2
        """,
        start,
        end,
    )

    lateral_penalty = 0.0
    vertical_penalty = 0.0
    for r in rows:
        duration_minutes = max((r["effective_resolved_at"] - r["detected_at"]).total_seconds() / 60.0, 0.0)
        weight = max(duration_minutes, 1.0)  # every recorded breach counts for at least one unit

        d_nm = float(r["lateral_distance_nm"]) if r["lateral_distance_nm"] is not None else 3.0
        if d_nm < 3.0:
            lateral_penalty += -math.exp(ALPHA * (3.0 - d_nm)) * weight

        dh_ft = float(r["vertical_distance_ft"]) if r["vertical_distance_ft"] is not None else 1000.0
        if dh_ft < 1000.0:
            vertical_penalty += -math.exp(BETA * (1000.0 - dh_ft)) * weight

    return {
        "lateral_penalty": lateral_penalty,
        "vertical_penalty": vertical_penalty,
        "wake_penalty": 0.0,  # documented limitation — see module docstring
        "anomaly_count": len(rows),
        "total": lateral_penalty + vertical_penalty,
    }


async def compute_throughput_reward(conn: asyncpg.Connection, start: datetime, end: datetime) -> dict:
    """N_landed/N_departed from on_ground transitions in aircraft_state_history.
    T_delay approximated as total time spent below STANDSTILL_VELOCITY_MPS while on_ground —
    a proxy for "standstill beyond nominal flight time" since Phase 1 doesn't track a
    per-flight nominal-duration baseline to compare against.
    """
    transitions = await conn.fetch(
        """
        SELECT on_ground,
               LAG(on_ground) OVER (PARTITION BY icao24 ORDER BY time_position) AS prev_on_ground
        FROM aircraft_state_history
        WHERE time_position >= $1 AND time_position < $2 AND is_commercial_ifr = true
        """,
        start,
        end,
    )
    n_landed = sum(1 for r in transitions if r["prev_on_ground"] is False and r["on_ground"] is True)
    n_departed = sum(1 for r in transitions if r["prev_on_ground"] is True and r["on_ground"] is False)

    delay_rows = await conn.fetch(
        """
        SELECT time_position,
               LAG(time_position) OVER (PARTITION BY icao24 ORDER BY time_position) AS prev_time
        FROM aircraft_state_history
        WHERE time_position >= $1 AND time_position < $2
          AND is_commercial_ifr = true AND on_ground = true AND velocity_mps < $3
        """,
        start,
        end,
        STANDSTILL_VELOCITY_MPS,
    )
    total_delay_seconds = sum(
        (r["time_position"] - r["prev_time"]).total_seconds()
        for r in delay_rows
        if r["prev_time"] is not None
    )

    total = GAMMA_LANDED * n_landed + GAMMA_DEPARTED * n_departed - GAMMA_DELAY * total_delay_seconds
    return {
        "n_landed": n_landed,
        "n_departed": n_departed,
        "total_delay_seconds": total_delay_seconds,
        "total": total,
    }


async def compute_efficiency_reward(conn: asyncpg.Connection, start: datetime, end: datetime) -> dict:
    """Heading-oscillation term from consecutive true_track_deg readings per aircraft, using
    circular difference (359deg -> 1deg counts as 2deg of change, not 358). D_optimal (actual
    path length minus geodesic distance to a destination fix) is not computed — Phase 1's
    schema has no flight-plan/destination data, only observed telemetry, so there is no
    "optimal path" to measure against without inventing a destination.
    """
    rows = await conn.fetch(
        """
        SELECT icao24, true_track_deg, time_position
        FROM aircraft_state_history
        WHERE time_position >= $1 AND time_position < $2
          AND is_commercial_ifr = true AND true_track_deg IS NOT NULL
        ORDER BY icao24, time_position
        """,
        start,
        end,
    )

    total_heading_change = 0.0
    prev_by_aircraft: dict[str, float] = {}
    for r in rows:
        icao = r["icao24"]
        track = float(r["true_track_deg"])
        if icao in prev_by_aircraft:
            diff = abs(track - prev_by_aircraft[icao])
            total_heading_change += min(diff, 360.0 - diff)
        prev_by_aircraft[icao] = track

    heading_penalty = -C_HEADING * total_heading_change
    return {
        "heading_oscillation_deg": total_heading_change,
        "heading_penalty": heading_penalty,
        "path_inefficiency_penalty": 0.0,  # documented limitation — see docstring
        "total": heading_penalty,
    }


async def compute_coordination_reward(conn: asyncpg.Connection, start: datetime, end: datetime) -> dict:
    """N_uncoordinated_crossings: an aircraft observed switching runway_complex membership
    (via ST_Contains, same as Phase 3's observation_builder) within the window, with no
    matching coordination_events row within +/-5 minutes of the crossing. N_deadlocks is not
    computed — Phase 3 logs coordination outcomes, not individual ATC instructions, so there
    is no way to detect *why* an aircraft stopped moving, only that it did (already captured
    by compute_throughput_reward's delay term).

    KNOWN BREAK (post-KSBA-rescope, deferred to Stage 3): this still queries the retired
    runway_complex table, which no longer exists — agent_orchestration moved to a per-runway
    `runway` table and a 4-role (Clearance/Ground/Tower/Approach) sequential handoff model
    with no "complex membership" concept to cross. This function will raise until it's
    reworked to detect role-handoff transitions instead of complex crossings.
    """
    rows = await conn.fetch(
        """
        SELECT h.icao24, h.time_position,
               (SELECT rc.complex_name FROM runway_complex rc
                WHERE ST_Contains(rc.boundary::geometry, h.geom::geometry)
                LIMIT 1) AS complex_name
        FROM aircraft_state_history h
        WHERE h.time_position >= $1 AND h.time_position < $2 AND h.is_commercial_ifr = true
        ORDER BY h.icao24, h.time_position
        """,
        start,
        end,
    )

    crossings: list[tuple[str, datetime]] = []
    prev_complex: dict[str, str] = {}
    for r in rows:
        icao = r["icao24"]
        complex_name = r["complex_name"]
        if complex_name is None:
            continue
        prev = prev_complex.get(icao)
        if prev is not None and prev != complex_name:
            crossings.append((icao, r["time_position"]))
        prev_complex[icao] = complex_name

    n_uncoordinated = 0
    window = timedelta(minutes=5)
    for icao, crossing_time in crossings:
        coordinated = await conn.fetchval(
            """
            SELECT count(*) FROM coordination_events
            WHERE aircraft_icao24 = $1 AND requested_at BETWEEN $2 AND $3
            """,
            icao,
            crossing_time - window,
            crossing_time + window,
        )
        if coordinated == 0:
            n_uncoordinated += 1

    penalty = -LAMBDA_UNCOORDINATED * n_uncoordinated
    return {
        "crossing_count": len(crossings),
        "uncoordinated_crossings": n_uncoordinated,
        "deadlocks": 0,  # documented limitation — see docstring
        "total": penalty,
    }


async def score_episode(conn: asyncpg.Connection, start: datetime, end: datetime) -> dict:
    safety = await compute_safety_reward(conn, start, end)
    throughput = await compute_throughput_reward(conn, start, end)
    efficiency = await compute_efficiency_reward(conn, start, end)
    coordination = await compute_coordination_reward(conn, start, end)

    weighted = {
        "R_safety": W_SAFETY * safety["total"],
        "R_throughput": W_THROUGHPUT * throughput["total"],
        "R_efficiency": W_EFFICIENCY * efficiency["total"],
        "R_coordination": W_COORDINATION * coordination["total"],
    }

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "components": {
            "safety": safety,
            "throughput": throughput,
            "efficiency": efficiency,
            "coordination": coordination,
        },
        "weighted": weighted,
        "R_t": sum(weighted.values()),
    }
