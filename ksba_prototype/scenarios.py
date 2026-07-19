"""Test scenarios for the KSBA 4-agent prototype.

Deliberately includes both "should act" and "should refuse/hold" cases for the same rule
(see the two Tower intersection scenarios) — a system that always says no to a runway
crossing isn't demonstrating judgment, it's just being uniformly cautious. The real test is
whether it draws the line in the right place.
"""

import json

SCENARIOS = [
    {
        "id": "clearance_standard_ifr",
        "role": "clearance_delivery",
        "description": "Standard IFR clearance request from a commercial jet.",
        "expect": "CRAFT-format clearance; departure frequency 120.55 (Runway 7 jet departure).",
        "input": (
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
        ),
    },
    {
        "id": "clearance_taxi_misuse",
        "role": "clearance_delivery",
        "description": "A GA pilot mistakenly calls Clearance Delivery asking to taxi.",
        "expect": "Deny the taxi request; redirect to Ground 121.7.",
        "input": (
            "Input: [Cessna N12345, requesting taxi to runway 15R]\n"
            "Current Airspace State: "
            + json.dumps({"aircraft": "N12345", "type": "C172", "position": "Signature Ramp"})
        ),
    },
    {
        "id": "ground_commercial_taxi",
        "role": "ground",
        "description": "Commercial jet ready to taxi to its departure runway.",
        "expect": "Route to Runway 7; hold-short instruction for any intersecting runway "
        "(15R/33L) with an explicit readback demand; handoff to Tower only once holding short.",
        "input": (
            "Input: [SkyWest 2450, Gate 3 ramp, ready to taxi, IFR clearance on file, "
            "assigned Runway 7]\n"
            "Current Airspace State: "
            + json.dumps(
                {
                    "aircraft": "SkyWest 2450",
                    "type": "CRJ700",
                    "position": "Gate 3 ramp",
                    "taxi_route_crosses": ["Runway 15R/33L"],
                }
            )
        ),
    },
    {
        "id": "ground_ga_taxi",
        "role": "ground",
        "description": "GA aircraft ready to taxi for pattern work.",
        "expect": "Route to Runway 15L or 15R (not 7/25); hold-short + readback if the route "
        "crosses another runway.",
        "input": (
            "Input: [N12345, Signature Ramp, ready to taxi, requesting pattern work]\n"
            "Current Airspace State: "
            + json.dumps(
                {
                    "aircraft": "N12345",
                    "type": "C172",
                    "position": "Signature Ramp",
                    "taxi_route_crosses": [],
                }
            )
        ),
    },
    {
        "id": "tower_intersection_conflict_MUST_HOLD",
        "role": "tower",
        "description": "GA aircraft holding short of 15R for departure; a commercial jet is "
        "1.5 NM final for Runway 25 — INSIDE the 2 NM conflict threshold.",
        "expect": "MUST NOT clear N12345 for departure/crossing — hold short due to the "
        "conflicting commercial traffic inside 2 NM.",
        "input": (
            "Input: [N12345, holding short Runway 15R, ready for departure]\n"
            "Current Airspace State: "
            + json.dumps(
                {
                    "runway_15R_hold": "N12345 (C172)",
                    "conflicting_traffic": {
                        "aircraft": "SkyWest 2450",
                        "type": "CRJ700",
                        "runway": 25,
                        "distance_from_threshold_nm": 1.5,
                        "status": "on final approach",
                    },
                }
            )
        ),
    },
    {
        "id": "tower_intersection_clear_SHOULD_CLEAR",
        "role": "tower",
        "description": "Same setup, but the commercial jet is 5 NM out — OUTSIDE the 2 NM "
        "threshold. Contrast case for the scenario above.",
        "expect": "Clear N12345 normally (crossing or departure) — no conflict at 5 NM.",
        "input": (
            "Input: [N12345, holding short Runway 15R, ready for departure]\n"
            "Current Airspace State: "
            + json.dumps(
                {
                    "runway_15R_hold": "N12345 (C172)",
                    "conflicting_traffic": {
                        "aircraft": "SkyWest 2450",
                        "type": "CRJ700",
                        "runway": 25,
                        "distance_from_threshold_nm": 5.0,
                        "status": "on final approach",
                    },
                }
            )
        ),
    },
    {
        "id": "tower_go_around",
        "role": "tower",
        "description": "An aircraft on short final while the preceding departure has not yet "
        "cleared the runway.",
        "expect": "Immediate go-around instruction to the arriving aircraft.",
        "input": (
            "Input: [SkyWest 2450, on short final Runway 7, 0.5 NM from threshold]\n"
            "Current Airspace State: "
            + json.dumps(
                {
                    "runway_7_status": "occupied",
                    "runway_7_occupant": {
                        "aircraft": "N12345",
                        "type": "C172",
                        "status": "still on runway, has not cleared, departure roll delayed",
                    },
                    "arrival": {
                        "aircraft": "SkyWest 2450",
                        "type": "CRJ700",
                        "distance_from_threshold_nm": 0.5,
                        "status": "short final",
                    },
                }
            )
        ),
    },
    {
        "id": "approach_speed_control",
        "role": "approach",
        "description": "A fast jet closing on a slower GA aircraft, both sequenced for "
        "Runway 7, separation eroding below 3 NM.",
        "expect": "Speed-reduction instruction to the faster jet to restore separation, not "
        "a vector or altitude change (rules specify speed control as the primary tool).",
        "input": (
            "Input: [SkyWest 2450, 20 NM out, 280 knots, descending through 6000]\n"
            "Current Airspace State: "
            + json.dumps(
                {
                    "aircraft": [
                        {
                            "callsign": "SkyWest 2450",
                            "type": "CRJ700",
                            "distance_from_ksba_nm": 20,
                            "speed_kts": 280,
                            "altitude_ft": 6000,
                            "assigned_runway": 7,
                        },
                        {
                            "callsign": "N12345",
                            "type": "C172",
                            "distance_from_ksba_nm": 15,
                            "speed_kts": 110,
                            "altitude_ft": 4000,
                            "assigned_runway": 7,
                        },
                    ],
                    "current_lateral_separation_nm": 3.2,
                    "closure_trend": "decreasing",
                }
            )
        ),
    },
    {
        "id": "approach_localizer_handoff",
        "role": "approach",
        "description": "Aircraft established on the localizer, 5 miles from the runway.",
        "expect": "Handoff instruction: contact Santa Barbara Tower on 119.7.",
        "input": (
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
        ),
    },
]
