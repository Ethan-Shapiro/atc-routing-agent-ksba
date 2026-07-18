"""Populates north_aircraft/south_aircraft/active_anomalies from Phase 1's PostGIS schema.

north/south classification is a direct ST_Contains against the runway_complex polygons
seeded in data_pipeline/sql/004_seed_reference_data.sql — the reason that table was added
during Phase 1 rather than left for this phase to migrate in.
"""

from typing import Any, Callable, Coroutine

import asyncpg

from state import MultiAgentATCState

_AIRCRAFT_IN_COMPLEX_SQL = """
SELECT c.icao24, c.callsign, c.longitude, c.latitude, c.baro_altitude_m,
       c.velocity_mps, c.true_track_deg, c.on_ground, c.wake_category, c.squawk
FROM aircraft_state_current c
JOIN runway_complex rc ON rc.complex_name = $1
WHERE c.is_commercial_ifr = true
  AND ST_Contains(rc.boundary::geometry, c.geom::geometry)
"""

_OPEN_ANOMALIES_SQL = """
SELECT id, anomaly_type, aircraft_icao24_1, aircraft_icao24_2,
       lateral_distance_nm, vertical_distance_ft, detected_at
FROM anomaly_events
WHERE resolved_at IS NULL
ORDER BY detected_at DESC
"""


def make_observation_builder(
    pool: asyncpg.Pool,
) -> Callable[[MultiAgentATCState], Coroutine[Any, Any, dict]]:
    async def observation_builder(state: MultiAgentATCState) -> dict:
        async with pool.acquire() as conn:
            north_rows = await conn.fetch(_AIRCRAFT_IN_COMPLEX_SQL, "NORTH")
            south_rows = await conn.fetch(_AIRCRAFT_IN_COMPLEX_SQL, "SOUTH")
            anomaly_rows = await conn.fetch(_OPEN_ANOMALIES_SQL)

        return {
            "north_aircraft": [dict(r) for r in north_rows],
            "south_aircraft": [dict(r) for r in south_rows],
            "active_anomalies": [dict(r) for r in anomaly_rows],
        }

    return observation_builder
