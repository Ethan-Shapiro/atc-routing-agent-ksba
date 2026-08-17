"""Populates aircraft_by_role/active_anomalies from Phase 1's PostGIS schema.

Role classification is a deterministic phase-of-flight heuristic (on_ground, velocity,
altitude, distance from the airport reference point), not an LLM judgment call — consistent
with the project's "LLMs cannot do math" guardrail already applied to the safety checks in
nodes/handoff_handler.py. It's necessarily an approximation: OpenSky's state vectors don't
carry taxiway/ramp-vs-runway-environment position, only lat/lon + on_ground, so "near a
runway threshold" stands in for "in Tower's jurisdiction" and "away from one" stands in for
"in Ground's." Good enough to route reasoning to the right role; not a real ATC boundary
tool.

CLEARANCE is never populated here (see state.py's note — pre-radar, not derivable from
ADS-B). Rows land in exactly one of GROUND/TOWER/APPROACH.
"""

from typing import Any, Callable, Coroutine

import asyncpg

from state import MultiRoleATCState

# Distances/altitudes are illustrative thresholds for a small GA-heavy field, not derived
# from a real KSBA letter of agreement:
#   - within 0.3 NM of a runway threshold while on the ground -> at/entering the runway
#     environment (Tower), otherwise still out on the taxiway network (Ground).
#   - airborne within 5 NM and below 2,500 ft AGL -> traffic pattern (Tower); beyond that,
#     still inbound/outbound on an instrument procedure (Approach).
TOWER_GROUND_PROXIMITY_NM = 0.3
TOWER_PATTERN_RADIUS_NM = 5.0
TOWER_PATTERN_ALTITUDE_AGL_FT = 2500.0
FIELD_ELEVATION_FT = 30.0  # KSBA field elevation, ~sea level

_CLASSIFY_AIRCRAFT_SQL = """
WITH nearest_runway AS (
    SELECT c.icao24,
           MIN(ST_Distance(c.geom, r.threshold_geom)) / 1852.0 AS distance_to_runway_nm
    FROM aircraft_state_current c
    CROSS JOIN runway r
    WHERE c.is_commercial_ifr = true
    GROUP BY c.icao24
)
SELECT c.icao24, c.callsign, c.longitude, c.latitude, c.baro_altitude_m,
       c.velocity_mps, c.true_track_deg, c.on_ground, c.wake_category, c.squawk,
       ST_Distance(c.geom, ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography) / 1852.0
           AS distance_from_airport_nm,
       nr.distance_to_runway_nm,
       CASE
           WHEN c.on_ground AND nr.distance_to_runway_nm <= $3 THEN 'TOWER'
           WHEN c.on_ground THEN 'GROUND'
           WHEN NOT c.on_ground
                AND ST_Distance(c.geom, ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography) / 1852.0 <= $4
                AND (c.baro_altitude_m IS NULL
                     OR (c.baro_altitude_m * 3.28084 - $6) <= $5)
               THEN 'TOWER'
           ELSE 'APPROACH'
       END AS flight_phase_role
FROM aircraft_state_current c
JOIN nearest_runway nr ON nr.icao24 = c.icao24
WHERE c.is_commercial_ifr = true
"""

_OPEN_ANOMALIES_SQL = """
SELECT id, anomaly_type, aircraft_icao24_1, aircraft_icao24_2,
       lateral_distance_nm, vertical_distance_ft, detected_at
FROM anomaly_events
WHERE resolved_at IS NULL
ORDER BY detected_at DESC
"""


async def classify_roles(
    conn: asyncpg.Connection, airport_lat: float, airport_lon: float
) -> dict[str, dict[str, Any]]:
    """Returns {icao24: {"role", "callsign"}} for every commercial-IFR aircraft currently in
    aircraft_state_current, using the exact same phase-of-flight SQL the observation node uses.
    Reused by the replay driver (agent_orchestration/replay.py) to detect role transitions —
    keeping one classifier so the replay-triggered role and the role the agent then reasons as
    can never drift apart."""
    rows = await conn.fetch(
        _CLASSIFY_AIRCRAFT_SQL,
        airport_lon,
        airport_lat,
        TOWER_GROUND_PROXIMITY_NM,
        TOWER_PATTERN_RADIUS_NM,
        TOWER_PATTERN_ALTITUDE_AGL_FT,
        FIELD_ELEVATION_FT,
    )
    return {r["icao24"]: {"role": r["flight_phase_role"], "callsign": r["callsign"]} for r in rows}


def make_observation_builder(
    pool: asyncpg.Pool,
    airport_lat: float,
    airport_lon: float,
) -> Callable[[MultiRoleATCState], Coroutine[Any, Any, dict]]:
    async def observation_builder(state: MultiRoleATCState) -> dict:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _CLASSIFY_AIRCRAFT_SQL,
                airport_lon,
                airport_lat,
                TOWER_GROUND_PROXIMITY_NM,
                TOWER_PATTERN_RADIUS_NM,
                TOWER_PATTERN_ALTITUDE_AGL_FT,
                FIELD_ELEVATION_FT,
            )
            anomaly_rows = await conn.fetch(_OPEN_ANOMALIES_SQL)

        aircraft_by_role: dict[str, list[dict[str, Any]]] = {
            "CLEARANCE": [],
            "GROUND": [],
            "TOWER": [],
            "APPROACH": [],
        }
        for r in rows:
            row = dict(r)
            role = row.pop("flight_phase_role")
            aircraft_by_role[role].append(row)

        return {
            "aircraft_by_role": aircraft_by_role,
            "active_anomalies": [dict(r) for r in anomaly_rows],
        }

    return observation_builder
