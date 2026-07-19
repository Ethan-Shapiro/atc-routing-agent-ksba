"""CLI entrypoint for Phase 5's offline reward scoring.

Usage (run inside the evaluation container — see docker-compose.yml's "evaluation" service):
    docker compose run --rm evaluation python score_episode.py \\
        --start 2026-07-18T00:00:00Z --end 2026-07-18T01:00:00Z
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone

import asyncpg

from config import settings
from reward import score_episode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score a recorded episode against the reward function.")
    parser.add_argument("--start", required=True, help="ISO 8601 start timestamp (UTC)")
    parser.add_argument("--end", required=True, help="ISO 8601 end timestamp (UTC)")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a formatted report")
    return parser.parse_args()


def _fmt(value: float) -> str:
    return f"{value:+.3f}"


def print_report(result: dict) -> None:
    print(f"Episode: {result['window']['start']} -> {result['window']['end']}")
    print()

    rows = [
        ("Safety", "safety", "R_safety"),
        ("Throughput", "throughput", "R_throughput"),
        ("Efficiency", "efficiency", "R_efficiency"),
        ("Coordination", "coordination", "R_coordination"),
    ]
    for label, component_key, weighted_key in rows:
        raw = result["components"][component_key]["total"]
        weighted = result["weighted"][weighted_key]
        print(f"  {label:14s} raw={_fmt(raw):>12s}   weighted={_fmt(weighted):>14s}")

    print()
    print(f"  R_t = {_fmt(result['R_t'])}")
    print()
    print("Component detail:")
    print(json.dumps(result["components"], indent=2, default=str))


async def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start).astimezone(timezone.utc)
    end = datetime.fromisoformat(args.end).astimezone(timezone.utc)
    if end <= start:
        raise SystemExit("--end must be after --start")

    conn = await asyncpg.connect(settings.postgres_dsn)
    try:
        result = await score_episode(conn, start, end)
    finally:
        await conn.close()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print_report(result)


if __name__ == "__main__":
    asyncio.run(main())
