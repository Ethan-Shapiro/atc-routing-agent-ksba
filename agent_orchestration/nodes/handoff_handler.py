"""Resolves a pending handoff_buffer entry from nodes/reasoning_engine.py. Two distinct jobs
live here, disambiguated by handoff_buffer["kind"]:

  - "CONFLICT_CHECK" — Tower's runway-intersection safety check on itself. Deliberately NOT
    an LLM judgment call — README.md's "LLMs Cannot Do Math" guardrail applies here as much
    as it did to the old 2-agent design's cross-boundary check: whether an intersecting
    runway's approach is clear is a spatial question, answered by a real PostGIS distance
    query against the `runway` table's intersects_with data, not model inference. Routes
    back to the ORIGIN role's reasoning node so it can act on the ACK/COUNTER_PROPOSAL
    verdict.

  - "HANDOFF" — a plain role-to-role handoff (advance_to_next_role). No safety decision is
    being made here — it's always acknowledged — this just writes the durable audit row
    (coordination_events) that Phase 5's reward function reads, since handoff_buffer itself
    is ephemeral LangGraph state. Does NOT re-invoke the target role's reasoning node within
    this graph run: the actual next radio exchange happens via a separate trigger (the
    manual /trigger/{role}/{icao24} endpoint today, a live phase-of-flight NOTIFY trigger in
    a later stage) — same as real ATC handoffs are separate radio calls, not one continuous
    exchange. Falls through to phraseology_generator if this turn ALSO finalized an
    instruction, otherwise ends the turn.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import asyncpg
from langchain_core.messages import HumanMessage

from state import MultiRoleATCState

# KSBA Tower's specific rule (see reasoning_engine.py's _RULES_BY_ROLE["TOWER"]) — distinct
# from the old 2-agent design's general 3.0 NM cross-boundary separation minimum.
INTERSECTION_CONFLICT_THRESHOLD_NM = 2.0

# Matches config.py's airport_lat/airport_lon default — used only as a fallback for
# CLEARANCE-role handoffs, where the aircraft has no real position yet.
AIRPORT_LAT_FALLBACK = 34.4262
AIRPORT_LON_FALLBACK = -119.8415

_NEAREST_CONFLICTING_TRAFFIC_SQL = """
WITH cross_runways AS (
    SELECT unnest(intersects_with) AS cross_runway_id FROM runway WHERE runway_id = $2
)
SELECT c.icao24, c.callsign, r.runway_id AS cross_runway_id,
       ST_Distance(c.geom, r.threshold_geom) / 1852.0 AS distance_nm
FROM aircraft_state_current c
JOIN runway r ON r.runway_id IN (SELECT cross_runway_id FROM cross_runways)
WHERE c.is_commercial_ifr = true AND c.icao24 != $1 AND c.on_ground = false
ORDER BY distance_nm ASC
LIMIT 1
"""

_INSERT_COORDINATION_EVENT_SQL = """
INSERT INTO coordination_events (
    origin_agent, target_agent, aircraft_icao24, action_type, coordinate_threshold,
    estimated_time_crossing, reason, coordination_status, verification_note, requested_at
) VALUES (
    $1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography, $7, $8, $9, $10, $11
)
"""


def make_handoff_handler(
    pool: asyncpg.Pool,
) -> Callable[[MultiRoleATCState], Coroutine[Any, Any, dict]]:
    async def handoff_handler(state: MultiRoleATCState) -> dict:
        buffer = state["handoff_buffer"]
        aircraft_id = buffer["aircraft_id"]

        if buffer["kind"] == "CONFLICT_CHECK":
            runway_id = buffer["runway_id"]
            async with pool.acquire() as conn:
                nearest = await conn.fetchrow(_NEAREST_CONFLICTING_TRAFFIC_SQL, aircraft_id, runway_id)
                threshold_lon, threshold_lat = await conn.fetchrow(
                    "SELECT ST_X(threshold_geom::geometry), ST_Y(threshold_geom::geometry) "
                    "FROM runway WHERE runway_id = $1",
                    runway_id,
                )

                if nearest is not None and nearest["distance_nm"] < INTERSECTION_CONFLICT_THRESHOLD_NM:
                    status = "COUNTER_PROPOSAL"
                    note = (
                        f"Traffic conflict: {nearest['callsign'] or nearest['icao24']} is "
                        f"{nearest['distance_nm']:.2f} NM from Runway {nearest['cross_runway_id']}'s "
                        f"threshold (< {INTERSECTION_CONFLICT_THRESHOLD_NM} NM minimum). Hold short."
                    )
                else:
                    status = "ACK"
                    note = (
                        f"No conflicting traffic within {INTERSECTION_CONFLICT_THRESHOLD_NM} NM "
                        "of the intersecting runway threshold."
                    )

                await conn.execute(
                    _INSERT_COORDINATION_EVENT_SQL,
                    buffer["origin_role"],
                    buffer["origin_role"],  # self-check, not a handoff to another role
                    aircraft_id,
                    "INTERSECTION_CONFLICT_CHECK",
                    threshold_lon,
                    threshold_lat,
                    None,
                    f"Runway {runway_id} conflict check against intersecting traffic",
                    status,
                    note,
                    datetime.now(timezone.utc),
                )

            return {
                "handoff_buffer": {
                    **buffer,
                    "status": "RESOLVED",
                    "coordination_status": status,
                    "verification_note": note,
                },
                "active_role": buffer["origin_role"],
                # HumanMessage, not AIMessage: this is new information delivered TO the
                # origin role for its next turn — an AIMessage here would leave the
                # conversation ending in an assistant turn when that role's reasoning node
                # calls the LLM again, which Claude Sonnet 5 rejects as an unsupported
                # assistant-message prefill (400).
                "messages": [
                    HumanMessage(content=f"[Runway conflict check for {aircraft_id}] {status}: {note}")
                ],
            }

        # kind == "HANDOFF"
        async with pool.acquire() as conn:
            aircraft_row = await conn.fetchrow(
                "SELECT longitude, latitude FROM aircraft_state_current WHERE icao24 = $1", aircraft_id
            )
            # A CLEARANCE-role handoff involves an aircraft that isn't on radar yet (see
            # state.py's note) — fall back to the airport reference point rather than fail
            # the handoff over a missing lat/lon.
            aircraft_lon = aircraft_row["longitude"] if aircraft_row else AIRPORT_LON_FALLBACK
            aircraft_lat = aircraft_row["latitude"] if aircraft_row else AIRPORT_LAT_FALLBACK
            await conn.execute(
                _INSERT_COORDINATION_EVENT_SQL,
                buffer["origin_role"],
                buffer["target_role"],
                aircraft_id,
                "ROLE_HANDOFF",
                aircraft_lon,
                aircraft_lat,
                None,
                buffer.get("reason"),
                "ACK",
                f"Handed off from {buffer['origin_role']} to {buffer['target_role']}.",
                datetime.now(timezone.utc),
            )

        return {
            "handoff_buffer": {**buffer, "status": "RESOLVED"},
        }

    return handoff_handler
