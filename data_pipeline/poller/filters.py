"""Commercial-IFR / no-VFR / no-helicopter classification.

No single OpenSky `/states/all` field reliably distinguishes commercial IFR traffic on its
own, so `is_commercial_ifr` is the AND of three independent signals. Non-matching aircraft
are NOT discarded — every aircraft in the polling bbox is still upserted, just flagged, so
the audit trail stays complete. All downstream consumers (the anomaly trigger's join, later
Phase 2/3 queries) filter on this column instead.
"""

import re
from dataclasses import dataclass

ROTORCRAFT_CATEGORY = 8
US_VFR_SQUAWK = "1200"
EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}

# 3-letter ICAO prefix + 1-4 digits + optional trailing letter, e.g. "AAL123", "DAL45B".
# US GA tail numbers ("N12345") fail this pattern outright.
_CALLSIGN_RE = re.compile(r"^[A-Z]{3}\d{1,4}[A-Z]?$")

# OpenSky /states/all field indices (18-field state vector).
IDX_ICAO24 = 0
IDX_CALLSIGN = 1
IDX_ORIGIN_COUNTRY = 2
IDX_TIME_POSITION = 3
IDX_LAST_CONTACT = 4
IDX_LONGITUDE = 5
IDX_LATITUDE = 6
IDX_BARO_ALTITUDE = 7
IDX_ON_GROUND = 8
IDX_VELOCITY = 9
IDX_TRUE_TRACK = 10
IDX_VERTICAL_RATE = 11
IDX_GEO_ALTITUDE = 13
IDX_SQUAWK = 14
IDX_SPI = 15
IDX_POSITION_SOURCE = 16
IDX_CATEGORY = 17


@dataclass
class StateVector:
    icao24: str
    callsign: str | None
    origin_country: str | None
    time_position: int | None
    last_contact: int | None
    longitude: float | None
    latitude: float | None
    baro_altitude_m: float | None
    on_ground: bool
    velocity_mps: float | None
    true_track_deg: float | None
    vertical_rate_mps: float | None
    geo_altitude_m: float | None
    squawk: str | None
    spi: bool
    position_source: int | None
    category: int | None
    is_commercial_ifr: bool = False
    is_emergency: bool = False


def _get(raw: list, idx: int):
    """OpenSky's live /states/all response doesn't always return the full 18-field vector —
    trailing fields like `category` (added to the API later) are sometimes simply absent
    rather than null, so a plain raw[idx] can IndexError. Missing trailing fields are treated
    as None, same as an explicit null would be."""
    return raw[idx] if idx < len(raw) else None


def parse_state_vector(raw: list) -> StateVector | None:
    """Parses one raw OpenSky state vector row. Returns None if it lacks a usable position."""
    latitude = _get(raw, IDX_LATITUDE)
    longitude = _get(raw, IDX_LONGITUDE)
    if latitude is None or longitude is None:
        return None

    callsign = (_get(raw, IDX_CALLSIGN) or "").strip() or None
    squawk = _get(raw, IDX_SQUAWK)

    sv = StateVector(
        icao24=_get(raw, IDX_ICAO24),
        callsign=callsign,
        origin_country=_get(raw, IDX_ORIGIN_COUNTRY),
        time_position=_get(raw, IDX_TIME_POSITION),
        last_contact=_get(raw, IDX_LAST_CONTACT),
        longitude=longitude,
        latitude=latitude,
        baro_altitude_m=_get(raw, IDX_BARO_ALTITUDE),
        on_ground=bool(_get(raw, IDX_ON_GROUND)),
        velocity_mps=_get(raw, IDX_VELOCITY),
        true_track_deg=_get(raw, IDX_TRUE_TRACK),
        vertical_rate_mps=_get(raw, IDX_VERTICAL_RATE),
        geo_altitude_m=_get(raw, IDX_GEO_ALTITUDE),
        squawk=squawk,
        spi=bool(_get(raw, IDX_SPI)),
        position_source=_get(raw, IDX_POSITION_SOURCE),
        category=_get(raw, IDX_CATEGORY),
    )
    sv.is_emergency = squawk in EMERGENCY_SQUAWKS
    return sv


def classify(sv: StateVector, known_airline_prefixes: set[str]) -> StateVector:
    """Mutates and returns `sv` with is_commercial_ifr set. Emergency squawks are never
    excluded by the squawk check, regardless of the other two signals."""
    not_helicopter = sv.category != ROTORCRAFT_CATEGORY
    not_vfr = sv.squawk != US_VFR_SQUAWK or sv.is_emergency
    callsign_matches = bool(
        sv.callsign
        and _CALLSIGN_RE.match(sv.callsign)
        and sv.callsign[:3] in known_airline_prefixes
    )

    sv.is_commercial_ifr = not_helicopter and not_vfr and callsign_matches
    return sv
