"""Sequential handoff chains for the KSBA 4-agent prototype.

Unlike scenarios.py (each role tested in isolation), a chain runs one aircraft through
multiple roles in sequence, feeding each agent's actual radio response into the next
agent's input as context — the real test of whether these 4 roles "work together"
rather than just individually producing correct-looking phraseology.

Each step's `build_input` receives a dict of prior step outputs keyed by step id.
"""

import json


def _departure_clearance(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450, destination LAX]\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "aircraft": "SkyWest 2450",
                "type": "CRJ700",
                "destination": "KLAX",
                "filed_route": "SBA V25 LAX",
                "requested_altitude": 16000,
                "assigned_departure_runway": 7,
            }
        )
    )


def _departure_taxi(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450, Gate 3 ramp, ready to taxi]\n"
        f"IFR clearance received from Clearance Delivery: {ctx['clearance']!r}\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "aircraft": "SkyWest 2450",
                "type": "CRJ700",
                "position": "Gate 3 ramp",
                "assigned_runway": 7,
                "taxi_route_crosses": ["Runway 15R/33L"],
            }
        )
    )


def _departure_takeoff(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450, holding short Runway 7 per Ground's taxi instruction, ready for departure]\n"
        f"Ground's taxi/hold-short instruction was: {ctx['taxi']!r}\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "runway_7_status": "clear",
                "holding_short_runway_7": "SkyWest 2450 (CRJ700)",
                "conflicting_traffic": None,
            }
        )
    )


def _departure_climbout_handoff(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450, airborne off Runway 7, passing 1,200 feet]\n"
        f"Takeoff clearance issued was: {ctx['takeoff']!r}\n"
        "Current Airspace State: "
        + json.dumps({"aircraft": "SkyWest 2450", "altitude_ft": 1200, "climbing": True})
    )


def _departure_approach_checkin(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450 checking in]\n"
        f"Tower's handoff instruction was: {ctx['climbout_handoff']!r}\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "aircraft": "SkyWest 2450",
                "type": "CRJ700",
                "status": "departure climb-out, radar contact",
                "altitude_ft": 1800,
                "climbing_to": 16000,
                "filed_route": "SBA V25 LAX",
            }
        )
    )


def _arrival_localizer(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450, established localizer Runway 7, 5 miles out]\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "aircraft": "SkyWest 2450",
                "type": "CRJ700",
                "status": "established on localizer",
                "runway": 7,
                "distance_from_threshold_nm": 5.0,
            }
        )
    )


def _arrival_tower_checkin(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450 checking in with Tower]\n"
        f"Approach's handoff instruction was: {ctx['approach_handoff']!r}\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "aircraft": "SkyWest 2450",
                "type": "CRJ700",
                "status": "on final, 3 miles",
                "runway": 7,
                "runway_7_status": "clear",
                "conflicting_traffic": None,
            }
        )
    )


def _arrival_landing_clear_runway(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450, landed Runway 7, decelerating, approaching Taxiway Bravo]\n"
        f"Landing clearance issued was: {ctx['tower_land']!r}\n"
        "Current Airspace State: "
        + json.dumps({"aircraft": "SkyWest 2450", "status": "rolling out, about to vacate runway 7"})
    )


def _arrival_ground_taxi_in(ctx: dict) -> str:
    return (
        "Input: [SkyWest 2450 checking in with Ground, clear of Runway 7, requesting taxi to gate]\n"
        f"Tower's instruction to contact Ground was: {ctx['runway_vacate']!r}\n"
        "Current Airspace State: "
        + json.dumps(
            {
                "aircraft": "SkyWest 2450",
                "type": "CRJ700",
                "position": "Taxiway Bravo, clear of Runway 7",
                "destination": "Gate 3 ramp",
                "taxi_route_crosses": [],
            }
        )
    )


DEPARTURE_CHAIN = [
    {"id": "clearance", "role": "clearance_delivery", "label": "Clearance Delivery — IFR clearance", "build_input": _departure_clearance},
    {"id": "taxi", "role": "ground", "label": "Ground — taxi to Runway 7", "build_input": _departure_taxi},
    {"id": "takeoff", "role": "tower", "label": "Tower — takeoff clearance", "build_input": _departure_takeoff},
    {"id": "climbout_handoff", "role": "tower", "label": "Tower — climb-out handoff to Approach", "build_input": _departure_climbout_handoff},
    {"id": "approach_checkin", "role": "approach", "label": "Approach — departure check-in", "build_input": _departure_approach_checkin},
]

ARRIVAL_CHAIN = [
    {"id": "approach_handoff", "role": "approach", "label": "Approach — localizer handoff to Tower", "build_input": _arrival_localizer},
    {"id": "tower_land", "role": "tower", "label": "Tower — landing clearance", "build_input": _arrival_tower_checkin},
    {"id": "runway_vacate", "role": "tower", "label": "Tower — runway vacated, contact Ground", "build_input": _arrival_landing_clear_runway},
    {"id": "ground_taxi_in", "role": "ground", "label": "Ground — taxi in to gate", "build_input": _arrival_ground_taxi_in},
]

CHAINS = {
    "departure": DEPARTURE_CHAIN,
    "arrival": ARRIVAL_CHAIN,
}
