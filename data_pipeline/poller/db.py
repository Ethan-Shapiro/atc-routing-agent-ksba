"""PostGIS access: reference-data loading, upserts, and audit logging."""

from datetime import datetime, timezone

import asyncpg

from filters import StateVector

_UPSERT_CURRENT = """
INSERT INTO aircraft_state_current (
    icao24, callsign, origin_country, longitude, latitude,
    baro_altitude_m, geo_altitude_m, on_ground, velocity_mps, true_track_deg,
    vertical_rate_mps, squawk, spi, position_source, category,
    is_commercial_ifr, time_position, last_contact, ingested_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, now()
)
ON CONFLICT (icao24) DO UPDATE SET
    callsign = EXCLUDED.callsign,
    origin_country = EXCLUDED.origin_country,
    longitude = EXCLUDED.longitude,
    latitude = EXCLUDED.latitude,
    baro_altitude_m = EXCLUDED.baro_altitude_m,
    geo_altitude_m = EXCLUDED.geo_altitude_m,
    on_ground = EXCLUDED.on_ground,
    velocity_mps = EXCLUDED.velocity_mps,
    true_track_deg = EXCLUDED.true_track_deg,
    vertical_rate_mps = EXCLUDED.vertical_rate_mps,
    squawk = EXCLUDED.squawk,
    spi = EXCLUDED.spi,
    position_source = EXCLUDED.position_source,
    category = EXCLUDED.category,
    is_commercial_ifr = EXCLUDED.is_commercial_ifr,
    time_position = EXCLUDED.time_position,
    last_contact = EXCLUDED.last_contact,
    ingested_at = now()
WHERE EXCLUDED.last_contact >= aircraft_state_current.last_contact
"""

_INSERT_HISTORY = """
INSERT INTO aircraft_state_history (
    icao24, callsign, origin_country, longitude, latitude,
    baro_altitude_m, geo_altitude_m, on_ground, velocity_mps, true_track_deg,
    vertical_rate_mps, squawk, spi, position_source, category,
    is_commercial_ifr, time_position, last_contact
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
)
ON CONFLICT (icao24, time_position) DO NOTHING
"""

_INSERT_AUDIT_LOG = """
INSERT INTO ingestion_audit_log (
    poll_started_at, poll_completed_at, http_status,
    aircraft_count_returned, aircraft_count_upserted, credits_used_estimate, error
) VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


def _to_utc(epoch_seconds: int | None) -> datetime | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


def _row_params(sv: StateVector) -> tuple:
    return (
        sv.icao24,
        sv.callsign,
        sv.origin_country,
        sv.longitude,
        sv.latitude,
        sv.baro_altitude_m,
        sv.geo_altitude_m,
        sv.on_ground,
        sv.velocity_mps,
        sv.true_track_deg,
        sv.vertical_rate_mps,
        sv.squawk,
        sv.spi,
        sv.position_source,
        sv.category,
        sv.is_commercial_ifr,
        _to_utc(sv.time_position),
        _to_utc(sv.last_contact),
    )


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5)


async def load_airline_prefixes(pool: asyncpg.Pool) -> set[str]:
    rows = await pool.fetch("SELECT icao_prefix FROM airline_designators")
    return {r["icao_prefix"] for r in rows}


async def upsert_states(pool: asyncpg.Pool, states: list[StateVector]) -> int:
    if not states:
        return 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for sv in states:
                params = _row_params(sv)
                await conn.execute(_UPSERT_CURRENT, *params)
                await conn.execute(_INSERT_HISTORY, *params)
    return len(states)


async def log_poll(
    pool: asyncpg.Pool,
    poll_started_at: datetime,
    poll_completed_at: datetime | None,
    http_status: int | None,
    aircraft_count_returned: int,
    aircraft_count_upserted: int,
    credits_used_estimate: int,
    error: str | None,
) -> None:
    await pool.execute(
        _INSERT_AUDIT_LOG,
        poll_started_at,
        poll_completed_at,
        http_status,
        aircraft_count_returned,
        aircraft_count_upserted,
        credits_used_estimate,
        error,
    )
