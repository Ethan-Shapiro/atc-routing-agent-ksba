"""Demo scenario catalog for the dashboard (dashboard/app.js).

Each scenario seeds specific synthetic aircraft_state_current rows (real OpenSky traffic
won't reliably provide "a conflict 0.16 NM out" on demand) and then plays back an ordered
chain of /trigger/{role}/{icao24} calls against real agent_orchestration infrastructure —
same mechanism, same seed data shape, as the manual psql + curl verification done while
building the KSBA rearchitecture (see git history), just packaged so the dashboard can drive
it with two HTTP calls instead of a terminal.

seed_aircraft rows are applied with ON CONFLICT (icao24) DO UPDATE so re-running (or running
a sibling scenario that reuses an icao24, e.g. the must_hold/should_clear pair sharing the
same holding-short aircraft) is idempotent — no manual cleanup between runs.
"""

from typing import Any

SCENARIOS: dict[str, dict[str, Any]] = {
    "standard_departure": {
        "title": "Standard Departure",
        "description": "One aircraft through the full chain: Clearance -> Ground -> Tower -> Approach.",
        "seed_aircraft": [
            {
                "icao24": "ksba01",
                "callsign": "SKW2450",
                "longitude": -119.8430,
                "latitude": 34.4260,
                "baro_altitude_m": 0,
                "on_ground": True,
                "velocity_mps": 0.0,
            }
        ],
        "steps": [
            {
                "role": "CLEARANCE",
                "icao24": "ksba01",
                "label": "Request IFR clearance",
                "context": "SkyWest 2450 requests IFR clearance to Los Angeles (KLAX), filed route SBA V25 LAX, requested altitude 16000, assigned departure Runway 7.",
            },
            {
                "role": "GROUND",
                "icao24": "ksba01",
                "label": "Ready to taxi",
                "context": "SkyWest 2450, Gate 3 ramp, ready to taxi, IFR clearance on file, assigned Runway 7.",
            },
            {
                "role": "TOWER",
                "icao24": "ksba01",
                "label": "Holding short, ready for departure",
                "context": "SkyWest 2450, holding short Runway 7, ready for departure.",
            },
            {
                "role": "APPROACH",
                "icao24": "ksba01",
                "label": "Climb-out check-in",
                "context": "SkyWest 2450, airborne off Runway 7, passing 1,200 feet, radar contact requested.",
            },
        ],
    },
    "standard_arrival": {
        "title": "Standard Arrival",
        "description": "One aircraft through the reverse chain: Approach -> Tower -> Ground.",
        "seed_aircraft": [
            {
                "icao24": "ksba04",
                "callsign": "SKW3210",
                "longitude": -119.9200,
                "latitude": 34.5200,
                "baro_altitude_m": 1500,
                "on_ground": False,
                "velocity_mps": 90.0,
            }
        ],
        "steps": [
            {
                "role": "APPROACH",
                "icao24": "ksba04",
                "label": "Localizer handoff",
                "context": "SkyWest 3210, established localizer Runway 7, 5 miles out.",
            },
            {
                "role": "TOWER",
                "icao24": "ksba04",
                "label": "On final, checking in",
                "context": "SkyWest 3210 checking in with Tower, on final for Runway 7.",
            },
            {
                "role": "GROUND",
                "icao24": "ksba04",
                "label": "Clear of runway, taxi in",
                "context": "SkyWest 3210, clear of Runway 7, requesting taxi to gate.",
            },
        ],
    },
    "tower_must_hold": {
        "title": "Tower: Must Hold (conflict)",
        "description": "GA aircraft holding short of 15R with a jet ~0.7 NM from Runway 25's threshold — inside the 2 NM minimum.",
        "seed_aircraft": [
            {
                "icao24": "ksba02",
                "callsign": "N12345",
                "longitude": -119.8396,
                "latitude": 34.4305,
                "baro_altitude_m": 0,
                "on_ground": True,
                "velocity_mps": 0.0,
            },
            {
                "icao24": "ksba03",
                "callsign": "SKW9099",
                "longitude": -119.8195,
                "latitude": 34.4340,
                "baro_altitude_m": 150,
                "on_ground": False,
                "velocity_mps": 60.0,
            },
        ],
        "steps": [
            {
                "role": "TOWER",
                "icao24": "ksba02",
                "label": "Holding short, ready for departure",
                "context": "November 12345, holding short Runway 15R, ready for departure.",
            },
        ],
    },
    "tower_should_clear": {
        "title": "Tower: Should Clear (no conflict)",
        "description": "Same holding-short aircraft, but the jet is now beyond 5 NM — outside the 2 NM minimum.",
        "seed_aircraft": [
            {
                "icao24": "ksba02",
                "callsign": "N12345",
                "longitude": -119.8396,
                "latitude": 34.4305,
                "baro_altitude_m": 0,
                "on_ground": True,
                "velocity_mps": 0.0,
            },
            {
                "icao24": "ksba03",
                "callsign": "SKW9099",
                "longitude": -119.9500,
                "latitude": 34.5500,
                "baro_altitude_m": 150,
                "on_ground": False,
                "velocity_mps": 60.0,
            },
        ],
        "steps": [
            {
                "role": "TOWER",
                "icao24": "ksba02",
                "label": "Holding short, ready for departure",
                "context": "November 12345, holding short Runway 15R, ready for departure.",
            },
        ],
    },
}

_UPSERT_SEED_AIRCRAFT_SQL = """
INSERT INTO aircraft_state_current (
    icao24, callsign, longitude, latitude, baro_altitude_m, on_ground, velocity_mps,
    is_commercial_ifr, last_contact
) VALUES ($1, $2, $3, $4, $5, $6, $7, true, now())
ON CONFLICT (icao24) DO UPDATE SET
    callsign = EXCLUDED.callsign,
    longitude = EXCLUDED.longitude,
    latitude = EXCLUDED.latitude,
    baro_altitude_m = EXCLUDED.baro_altitude_m,
    on_ground = EXCLUDED.on_ground,
    velocity_mps = EXCLUDED.velocity_mps,
    last_contact = now()
"""


async def seed_scenario(pool, scenario_id: str) -> None:
    scenario = SCENARIOS[scenario_id]
    async with pool.acquire() as conn:
        for aircraft in scenario["seed_aircraft"]:
            await conn.execute(
                _UPSERT_SEED_AIRCRAFT_SQL,
                aircraft["icao24"],
                aircraft["callsign"],
                aircraft["longitude"],
                aircraft["latitude"],
                aircraft["baro_altitude_m"],
                aircraft["on_ground"],
                aircraft["velocity_mps"],
            )
