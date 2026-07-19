"""Handles a pending inter_agent_buffer coordination request from the target complex's side.

Deliberately NOT an LLM judgment call — README.md's "LLMs Cannot Do Math" guardrail applies
here as much as anywhere: whether the crossing point is clear is a spatial question, answered
by a PostGIS distance query (mirroring Phase 1's own anomaly-detection approach), not model
inference. The target agent's reasoning node only sees the outcome (ACK / COUNTER_PROPOSAL)
on its next turn.
"""

from typing import Any, Callable, Coroutine

import asyncpg
from langchain_core.messages import HumanMessage

from state import MultiAgentATCState

SEPARATION_THRESHOLD_NM = 3.0

_NEAREST_TRAFFIC_SQL = """
SELECT icao24, callsign,
       ST_Distance(geom, ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography) / 1852.0 AS distance_nm
FROM aircraft_state_current
WHERE is_commercial_ifr = true AND icao24 != $3
ORDER BY distance_nm ASC
LIMIT 1
"""


def make_inter_agent_comm_handler(
    pool: asyncpg.Pool,
) -> Callable[[MultiAgentATCState], Coroutine[Any, Any, dict]]:
    async def inter_agent_comm_handler(state: MultiAgentATCState) -> dict:
        buffer = state["inter_agent_buffer"]
        lat, lon = buffer["proposed_action"]["coordinate_threshold"]
        aircraft_id = buffer["aircraft_id"]

        async with pool.acquire() as conn:
            nearest = await conn.fetchrow(_NEAREST_TRAFFIC_SQL, lon, lat, aircraft_id)

        if nearest is not None and nearest["distance_nm"] < SEPARATION_THRESHOLD_NM:
            status = "COUNTER_PROPOSAL"
            note = (
                f"Traffic conflict: {nearest['callsign'] or nearest['icao24']} is "
                f"{nearest['distance_nm']:.2f} NM from the crossing point (< "
                f"{SEPARATION_THRESHOLD_NM} NM minimum). Hold crossing and retry."
            )
        else:
            status = "ACK"
            note = "No conflicting traffic within separation minimums at the crossing point."

        # requires_coordination flips to False here — this is the router's actual signal
        # that this request is resolved. Don't rely on active_agent for that instead: the
        # reasoning node unconditionally overwrites active_agent to its own name on every
        # call, so a guard based on active_agent gets silently clobbered before the router
        # ever sees it (the bug that caused the original infinite loop).
        updated_buffer = {
            **buffer,
            "requires_coordination": False,
            "coordination_status": status,
            "verification_note": note,
        }

        return {
            "inter_agent_buffer": updated_buffer,
            "active_agent": "COORDINATOR",
            # HumanMessage, not AIMessage: this is new information delivered TO the origin
            # agent for its next turn, not something the agent itself said. An AIMessage
            # here would leave the conversation ending in an assistant turn when the
            # origin agent's reasoning node calls the LLM again next — Claude Sonnet 5
            # rejects that as an unsupported assistant-message prefill (400).
            "messages": [
                HumanMessage(
                    content=(
                        f"[{buffer['target_agent']} verification for {aircraft_id}] "
                        f"{status}: {note}"
                    )
                )
            ],
        }

    return inter_agent_comm_handler
