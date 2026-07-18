"""Thin parameterized-SQL wrapper over Phase 1's PostGIS schema. Never raw SQL exposed to
the LLM — the tool signature is a flight_id, everything else is fixed queries.
"""

import psycopg2
import psycopg2.extras

_CURRENT_STATE_SQL = """
SELECT icao24, callsign, origin_country, longitude, latitude,
       baro_altitude_m, geo_altitude_m, on_ground, velocity_mps, true_track_deg,
       vertical_rate_mps, squawk, wake_category, is_commercial_ifr,
       last_contact, ingested_at
FROM aircraft_state_current
WHERE icao24 = %s AND is_commercial_ifr = true
"""

_OPEN_ANOMALIES_SQL = """
SELECT id, anomaly_type, aircraft_icao24_1, aircraft_icao24_2,
       lateral_distance_nm, vertical_distance_ft, detected_at
FROM anomaly_events
WHERE resolved_at IS NULL
  AND (aircraft_icao24_1 = %s OR aircraft_icao24_2 = %s)
ORDER BY detected_at DESC
"""

_RECENT_HISTORY_SQL = """
SELECT longitude, latitude, baro_altitude_m, velocity_mps, true_track_deg, time_position
FROM aircraft_state_history
WHERE icao24 = %s AND is_commercial_ifr = true
ORDER BY time_position DESC
LIMIT 10
"""


def query_radar(flight_id: str, conn: psycopg2.extensions.connection) -> dict:
    """MCP tool entry point. `conn` is injected by server.py (one long-lived connection,
    matching the pattern used for the FAISS index in query_faa_rules)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_CURRENT_STATE_SQL, (flight_id,))
        current = cur.fetchone()
        if current is None:
            return {"flight_id": flight_id, "found": False}

        cur.execute(_OPEN_ANOMALIES_SQL, (flight_id, flight_id))
        anomalies = cur.fetchall()

        cur.execute(_RECENT_HISTORY_SQL, (flight_id,))
        history = cur.fetchall()

    return {
        "flight_id": flight_id,
        "found": True,
        "current_state": dict(current),
        "open_anomalies": [dict(a) for a in anomalies],
        "recent_track": [dict(h) for h in history],
    }
