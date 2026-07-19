"""MultiRoleATCState schema — KSBA 4-role sequential handoff design.

Replaces the LAX-era 2-agent MultiAgentATCState (NORTH_TOWER/SOUTH_TOWER coordinating over a
geographic split). aircraft_by_role is populated by nodes/observation_builder.py via a
deterministic phase-of-flight classifier (on_ground/altitude/velocity/distance-from-airport
heuristics against the per-runway `runway` table from data_pipeline/sql/001_schema.sql) — not
a spatial ST_Contains membership check, since roles here are phases of flight, not regions.

CLEARANCE is intentionally never populated by observation_builder: an aircraft awaiting an
IFR clearance at the gate typically isn't broadcasting ADS-B yet, so it can't be found in
aircraft_state_current the way a taxiing/airborne aircraft can. Clearance Delivery is only
reachable via main.py's manual /trigger/{role}/{icao24} endpoint (see main.py's own note on
why this is a structural limitation, not a v1 shortcut).

final_instruction is unchanged from the 2-agent design — the Phase 4 hybrid split (structured
decision -> separate phraseology rendering step) is already role-agnostic.
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class MultiRoleATCState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    active_role: str  # "CLEARANCE" | "GROUND" | "TOWER" | "APPROACH" | "IDLE"

    aircraft_by_role: dict[str, list[dict[str, Any]]]  # keyed by the 4 roles in roles.py

    # Was inter_agent_buffer. Now the *primary* cross-node mechanism, not a rare
    # cross-boundary exception — every aircraft moves through all 4 roles in sequence. Two
    # kinds of request live here, disambiguated by "kind" (see nodes/handoff_handler.py):
    #   "HANDOFF"        — a role handing an aircraft to an explicit target_role.
    #   "CONFLICT_CHECK" — Tower's deterministic runway-intersection safety check on itself.
    handoff_buffer: dict[str, Any]

    active_anomalies: list[dict[str, Any]]

    final_instruction: dict[str, Any] | None
