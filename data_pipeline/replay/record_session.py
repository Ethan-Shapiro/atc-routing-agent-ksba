"""Label a window of already-recorded aircraft_state_history as a `recording_session`.

Recording itself is just the poller running (it writes aircraft_state_history continuously).
This CLI marks a [start, end) slice of that history as a named session the replay driver can
play back. Run it from the host after some data has accumulated:

    # the most recent 30 minutes
    python record_session.py --label "morning arrivals" --last-minutes 30

    # an explicit UTC window
    python record_session.py --label demo --start 2026-07-23T03:10:00Z --end 2026-07-23T03:40:00Z

    python record_session.py --list          # show existing sessions + their aircraft counts

Connects to PostGIS on localhost:5432 (the port docker-compose maps out), using the same
credentials as .env — so it works from the host without going through a container.
"""

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


def _dsn() -> str:
    # .env lives at the repo root, two levels up from data_pipeline/replay/.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    user = os.environ.get("POSTGIS_USER", "atc_admin")
    password = os.environ.get("POSTGIS_PASSWORD", "")
    db = os.environ.get("POSTGIS_DB", "atc_geo")
    # Host-run default is localhost (the mapped port), NOT the in-network "postgis" host.
    host = os.environ.get("POSTGIS_HOST_LOCAL", "localhost")
    port = os.environ.get("POSTGIS_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _list_sessions(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT s.id, s.label, s.started_at, s.ended_at,
               (SELECT count(DISTINCT h.icao24) FROM aircraft_state_history h
                WHERE h.time_position >= s.started_at AND h.time_position < s.ended_at
                  AND h.is_commercial_ifr) AS ifr_aircraft
        FROM recording_session s ORDER BY s.id
        """
    )
    if not rows:
        print("No recording sessions yet.")
        return
    for r in rows:
        span = (r["ended_at"] - r["started_at"]).total_seconds() / 60.0
        print(
            f"[{r['id']}] {r['label']!r}  {r['started_at']:%Y-%m-%d %H:%M} → "
            f"{r['ended_at']:%H:%M}  ({span:.0f} min, {r['ifr_aircraft']} IFR aircraft)"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="List existing sessions and exit")
    parser.add_argument("--label", help="Human-readable name for the session")
    parser.add_argument("--start", help="Window start (ISO 8601 UTC, e.g. 2026-07-23T03:10:00Z)")
    parser.add_argument("--end", help="Window end (ISO 8601 UTC)")
    parser.add_argument("--last-minutes", type=float, help="Convenience: window = [now - N min, now]")
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        if args.list:
            await _list_sessions(conn)
            return

        if not args.label:
            parser.error("--label is required when creating a session")
        if args.last_minutes is not None:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=args.last_minutes)
        elif args.start and args.end:
            start, end = _parse_ts(args.start), _parse_ts(args.end)
        else:
            parser.error("provide either --last-minutes N, or both --start and --end")

        row = await conn.fetchrow(
            """
            INSERT INTO recording_session (label, started_at, ended_at, notes)
            VALUES ($1, $2, $3, $4) RETURNING id
            """,
            args.label, start, end, args.notes,
        )
        count = await conn.fetchval(
            """
            SELECT count(DISTINCT icao24) FROM aircraft_state_history
            WHERE time_position >= $1 AND time_position < $2 AND is_commercial_ifr
            """,
            start, end,
        )
        print(f"Created session [{row['id']}] {args.label!r}: {start:%H:%M} → {end:%H:%M} "
              f"({count} IFR aircraft in window)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
