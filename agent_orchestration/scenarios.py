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
                # No position update — pre-radar, matches the seeded gate-ramp row.
            },
            {
                "role": "GROUND",
                "icao24": "ksba01",
                "label": "Ready to taxi",
                "context": "SkyWest 2450, Gate 3 ramp, ready to taxi, IFR clearance on file, assigned Runway 7.",
                # No position update — already seeded at Gate 3 ramp.
            },
            {
                "role": "TOWER",
                "icao24": "ksba01",
                "label": "Holding short, ready for departure",
                "context": "SkyWest 2450, holding short Runway 7, ready for departure.",
                # Moved from the gate ramp to just short of Runway 7's threshold — without
                # this the aircraft's real position still shows it parked at the gate, which
                # a model grounded in query_radar will (correctly) refuse to act on.
                "position": {
                    "longitude": -119.8505,
                    "latitude": 34.4236,
                    "baro_altitude_m": 0,
                    "on_ground": True,
                    "velocity_mps": 0.0,
                },
            },
            {
                "role": "APPROACH",
                "icao24": "ksba01",
                "label": "Climb-out check-in",
                "context": "SkyWest 2450, airborne off Runway 7, passing 1,200 feet, radar contact requested.",
                "position": {
                    "longitude": -119.8280,
                    "latitude": 34.4310,
                    "baro_altitude_m": 366,  # ~1,200 ft
                    "on_ground": False,
                    "velocity_mps": 70.0,
                },
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
                "position": {
                    "longitude": -119.9459,
                    "latitude": 34.3950,
                    "baro_altitude_m": 490,  # ~1,600 ft, 3-degree glideslope at 5 NM
                    "on_ground": False,
                    "velocity_mps": 70.0,
                },
            },
            {
                "role": "TOWER",
                "icao24": "ksba04",
                "label": "On final, checking in",
                "context": "SkyWest 3210 checking in with Tower, on final for Runway 7.",
                "position": {
                    "longitude": -119.8795,
                    "latitude": 34.4149,
                    "baro_altitude_m": 161,  # ~500 ft AGL, 1.5 NM final
                    "on_ground": False,
                    "velocity_mps": 60.0,
                },
            },
            {
                "role": "GROUND",
                "icao24": "ksba04",
                "label": "Clear of runway, taxi in",
                "context": "SkyWest 3210, clear of Runway 7, requesting taxi to gate.",
                "position": {
                    "longitude": -119.8430,
                    "latitude": 34.4260,
                    "baro_altitude_m": 0,
                    "on_ground": True,
                    "velocity_mps": 5.0,
                },
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


# Every icao24 used anywhere in the catalog. Deleted wholesale before each reset (not just
# the requested scenario's own rows) — observed live: leftover aircraft from an EARLIER
# scenario run stayed in aircraft_state_current, showed up in a later scenario's
# aircraft_by_role list (see nodes/observation_builder.py), and the model picked one of those
# instead of the aircraft actually named in the seed message. Scoped to only these known
# synthetic icao24s so a reset never touches real OpenSky-ingested rows.
ALL_SCENARIO_ICAO24S = [
    aircraft["icao24"] for scenario in SCENARIOS.values() for aircraft in scenario["seed_aircraft"]
]

_DELETE_ALL_SCENARIO_AIRCRAFT_SQL = "DELETE FROM aircraft_state_current WHERE icao24 = ANY($1::text[])"


async def seed_scenario(pool, scenario_id: str) -> None:
    scenario = SCENARIOS[scenario_id]
    async with pool.acquire() as conn:
        await conn.execute(_DELETE_ALL_SCENARIO_AIRCRAFT_SQL, ALL_SCENARIO_ICAO24S)
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
