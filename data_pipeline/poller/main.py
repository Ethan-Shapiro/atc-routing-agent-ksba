"""Always-on OpenSky polling loop.

Deliberately NOT an Airflow DAG: Airflow's scheduler/DagRun model isn't built for a
12-second cadence (see docker-compose.yml / plan notes). Airflow instead handles minute-scale
housekeeping (retention, healthcheck) over the data this service writes.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import db
from config import settings
from filters import classify, parse_state_vector
from opensky_client import OpenSkyClient, compute_bbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("opensky-poller")


class CreditBudget:
    """Tracks estimated daily request-credit usage, resetting at UTC midnight. A 50-mile bbox
    query costs 1 credit; skipping a poll rather than risking a 429 / tier violation is the
    plan's explicit rate-limit safeguard beyond just the polling interval."""

    def __init__(self, daily_budget: int):
        self._daily_budget = daily_budget
        self._used = 0
        self._reset_day = datetime.now(timezone.utc).date()

    def _maybe_reset(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._reset_day:
            self._used = 0
            self._reset_day = today

    def can_spend(self, cost: int = 1) -> bool:
        self._maybe_reset()
        return self._used + cost <= self._daily_budget

    def spend(self, cost: int = 1) -> None:
        self._maybe_reset()
        self._used += cost


class CircuitBreaker:
    """Pauses polling after repeated consecutive failures instead of hammering a failing API."""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 60.0):
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._consecutive_failures = 0
        self._paused_until: float = 0.0

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._paused_until = time.monotonic() + self._cooldown
            log.warning(
                "Circuit breaker tripped after %d consecutive failures; pausing %.0fs",
                self._consecutive_failures,
                self._cooldown,
            )

    def is_open(self) -> bool:
        return time.monotonic() < self._paused_until


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _fetch_with_retry(client: OpenSkyClient, bbox: dict) -> httpx.Response:
    resp = await client.fetch_states(bbox)
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    return resp


async def poll_once(
    client: OpenSkyClient,
    pool,
    bbox: dict,
    airline_prefixes: set[str],
) -> None:
    started_at = datetime.now(timezone.utc)
    http_status: int | None = None
    returned = 0
    upserted = 0
    error: str | None = None

    try:
        resp = await _fetch_with_retry(client, bbox)
        http_status = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
        raw_states = payload.get("states") or []
        returned = len(raw_states)

        parsed = [parse_state_vector(raw) for raw in raw_states]
        states = [classify(sv, airline_prefixes) for sv in parsed if sv is not None]
        upserted = await db.upsert_states(pool, states)
    except Exception as exc:  # noqa: BLE001 - logged to the durable audit table below
        error = str(exc)
        log.exception("Poll cycle failed")
        raise
    finally:
        await db.log_poll(
            pool,
            poll_started_at=started_at,
            poll_completed_at=datetime.now(timezone.utc),
            http_status=http_status,
            aircraft_count_returned=returned,
            aircraft_count_upserted=upserted,
            credits_used_estimate=1,
            error=error,
        )


async def run() -> None:
    pool = await db.create_pool(settings.postgres_dsn)
    client = OpenSkyClient(settings.opensky_client_id, settings.opensky_client_secret)
    budget = CreditBudget(settings.opensky_daily_credit_budget)
    breaker = CircuitBreaker()
    bbox = compute_bbox(settings.lax_lat, settings.lax_lon, settings.coverage_radius_miles)
    airline_prefixes = await db.load_airline_prefixes(pool)

    log.info("Starting OpenSky poller: bbox=%s interval=%.1fs", bbox, settings.poll_interval_seconds)

    next_tick = time.monotonic()
    try:
        while True:
            next_tick += settings.poll_interval_seconds

            if breaker.is_open():
                log.warning("Circuit breaker open, skipping this cycle")
            elif not budget.can_spend():
                log.warning("Daily credit budget exhausted, skipping this cycle")
            else:
                try:
                    await poll_once(client, pool, bbox, airline_prefixes)
                    budget.spend()
                    breaker.record_success()
                except Exception:  # noqa: BLE001 - already logged in poll_once
                    breaker.record_failure()

            sleep_for = max(0.0, next_tick - time.monotonic())
            await asyncio.sleep(sleep_for)
    finally:
        await client.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
