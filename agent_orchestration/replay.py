"""Recorded-day replay driver.

Plays a `recording_session` window of aircraft_state_history back on a clock, re-populating
aircraft_state_current so the *real* pipeline fires against real traffic: the existing
proximity-conflict trigger + pg_notify('new_anomaly') + LISTEN path runs unchanged, and role
transitions (an arrival crossing from Approach's range into Tower's, etc.) are detected here
and turned into agent runs. Agent decisions are cached (replay_agent_response) so the first
pass through a window costs one API call per event and every subsequent loop is free — the
whole point of "loop over the day without re-pinging the API."

Runs as a background asyncio task inside agent_orchestration (same pattern as the anomaly
LISTEN task), so the dashboard drives it through the one FastAPI surface it already talks to.

NOTE: replay owns aircraft_state_current while it runs. Stop the opensky-poller during a
replay so live traffic doesn't mix into the recorded window (they'd both write the same
table). Recording and replaying are naturally separate phases, so this isn't a real
constraint in practice — just don't do both at once.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import asyncpg

from nodes.observation_builder import classify_roles

log = logging.getLogger("agent-orchestration.replay")

# One real second per tick; the replay clock advances TICK_REAL_SECONDS * speed of recorded
# time each tick. Smoothness of the planes is the dashboard's job (it interpolates the track
# data against the clock) — the tick only sets how often state is re-populated and role
# transitions are checked, so a coarse cadence is fine.
TICK_REAL_SECONDS = 1.0
# An aircraft whose most recent broadcast is older than this (in replay time) is treated as
# having left coverage and removed from aircraft_state_current — so planes depart the map.
STALE_AFTER_SECONDS = 120
# Roles worth waking an agent for when an aircraft first appears (already-parked GROUND
# aircraft at window start aren't an event; an inbound aircraft showing up on Approach is).
APPEAR_TRIGGER_ROLES = {"TOWER", "APPROACH"}
ACTIVE_ROLES = {"GROUND", "TOWER", "APPROACH"}

_UPSERT_SLICE_SQL = """
INSERT INTO aircraft_state_current (
    icao24, callsign, origin_country, longitude, latitude,
    baro_altitude_m, geo_altitude_m, on_ground, velocity_mps, true_track_deg,
    vertical_rate_mps, squawk, spi, position_source, category, wake_category,
    is_commercial_ifr, time_position, last_contact, ingested_at
)
SELECT DISTINCT ON (icao24)
    icao24, callsign, origin_country, longitude, latitude,
    baro_altitude_m, geo_altitude_m, on_ground, velocity_mps, true_track_deg,
    vertical_rate_mps, squawk, spi, position_source, category, wake_category,
    is_commercial_ifr, time_position, last_contact, now()
FROM aircraft_state_history
WHERE time_position > $1 AND time_position <= $2
ORDER BY icao24, time_position DESC
ON CONFLICT (icao24) DO UPDATE SET
    callsign = EXCLUDED.callsign, origin_country = EXCLUDED.origin_country,
    longitude = EXCLUDED.longitude, latitude = EXCLUDED.latitude,
    baro_altitude_m = EXCLUDED.baro_altitude_m, geo_altitude_m = EXCLUDED.geo_altitude_m,
    on_ground = EXCLUDED.on_ground, velocity_mps = EXCLUDED.velocity_mps,
    true_track_deg = EXCLUDED.true_track_deg, vertical_rate_mps = EXCLUDED.vertical_rate_mps,
    squawk = EXCLUDED.squawk, spi = EXCLUDED.spi, position_source = EXCLUDED.position_source,
    category = EXCLUDED.category, wake_category = EXCLUDED.wake_category,
    is_commercial_ifr = EXCLUDED.is_commercial_ifr, time_position = EXCLUDED.time_position,
    last_contact = EXCLUDED.last_contact, ingested_at = now()
WHERE EXCLUDED.last_contact >= aircraft_state_current.last_contact
"""

# on_event(kind, icao24, callsign, role, replay_time, prev_role) — implemented by main.py
# (does the cache check + optional graph run + cache store). Fired fire-and-forget so a slow
# LLM call never stalls the replay clock.
OnEvent = Callable[[str, str, str | None, str, datetime, str | None], Awaitable[None]]


class ReplayController:
    def __init__(
        self, pool: asyncpg.Pool, dsn: str, airport_lat: float, airport_lon: float, on_event: OnEvent
    ):
        self._pool = pool
        # The tick loop uses a DEDICATED connection (opened per run), never the shared pool:
        # on the first uncached pass the driver fires several concurrent agent runs that hold
        # pool connections for the length of an LLM call, and if the tick loop had to wait on
        # the same pool it would stall — the replay clock freezing near the window start until
        # those calls drained was exactly the "stuck at 03:05" bug.
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None
        self._airport_lat = airport_lat
        self._airport_lon = airport_lon
        self._on_event = on_event
        self._task: asyncio.Task | None = None
        self._playing = False
        self._session_id: int | None = None
        self._label: str | None = None
        self._clock: datetime | None = None
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._speed = 60.0
        self._loop = True
        self._prev_roles: dict[str, str] = {}

    def status(self) -> dict:
        return {
            "playing": self._playing,
            "session_id": self._session_id,
            "label": self._label,
            "clock": self._clock.isoformat() if self._clock else None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            "speed": self._speed,
            "loop": self._loop,
        }

    async def start(self, session_id: int, speed: float = 60.0, loop: bool = True) -> dict:
        await self.stop()
        row = await self._pool.fetchrow(
            "SELECT label, started_at, ended_at FROM recording_session WHERE id = $1", session_id
        )
        if row is None:
            raise ValueError(f"recording_session id={session_id} not found")
        self._session_id = session_id
        self._label = row["label"]
        self._started_at = row["started_at"]
        self._ended_at = row["ended_at"]
        self._speed = speed
        self._loop = loop
        self._clock = self._started_at
        self._prev_roles = {}
        self._playing = True
        self._task = asyncio.create_task(self._run())
        log.info("Replay started: session=%s speed=%sx loop=%s", session_id, speed, loop)
        return self.status()

    async def stop(self) -> None:
        self._playing = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _clear_current(self) -> None:
        await self._conn.execute("TRUNCATE aircraft_state_current")

    async def _run(self) -> None:
        try:
            self._conn = await asyncpg.connect(self._dsn)
            await self._clear_current()
            while self._playing:
                await asyncio.sleep(TICK_REAL_SECONDS)
                prev_clock = self._clock
                self._clock = prev_clock + timedelta(seconds=TICK_REAL_SECONDS * self._speed)

                await self._conn.execute(_UPSERT_SLICE_SQL, prev_clock, self._clock)
                await self._conn.execute(
                    "DELETE FROM aircraft_state_current WHERE last_contact < $1",
                    self._clock - timedelta(seconds=STALE_AFTER_SECONDS),
                )
                roles = await classify_roles(self._conn, self._airport_lat, self._airport_lon)

                self._detect_transitions(roles)

                if self._clock >= self._ended_at:
                    if self._loop:
                        log.info("Replay looping: session=%s", self._session_id)
                        self._clock = self._started_at
                        self._prev_roles = {}
                        await self._clear_current()
                    else:
                        self._playing = False
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Replay loop failed for session=%s", self._session_id)
            self._playing = False

    def _detect_transitions(self, roles: dict[str, dict]) -> None:
        for icao24, info in roles.items():
            role = info["role"]
            prev = self._prev_roles.get(icao24)
            if prev == role:
                continue
            fire = (prev is not None and prev != role and role in ACTIVE_ROLES) or (
                prev is None and role in APPEAR_TRIGGER_ROLES
            )
            if fire:
                # Fire-and-forget: a slow agent run must not stall the replay clock. The
                # handler in main.py dedupes/caches by a stable content fingerprint.
                asyncio.create_task(
                    self._on_event("ROLE_TRANSITION", icao24, info["callsign"], role, self._clock, prev)
                )
        self._prev_roles = {icao24: info["role"] for icao24, info in roles.items()}
