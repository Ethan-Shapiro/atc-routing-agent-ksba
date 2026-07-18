"""MultiAgentATCState schema, verbatim from multi-agent_orchestration_rf_layer.md.

north_aircraft/south_aircraft are populated by nodes/observation_builder.py via a direct
ST_Contains(runway_complex.boundary, aircraft.geom) query against the table Phase 1 seeds
(data_pipeline/sql/004_seed_reference_data.sql).
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class MultiAgentATCState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    active_agent: str  # "NORTH_TOWER" | "SOUTH_TOWER" | "COORDINATOR"

    north_aircraft: list[dict[str, Any]]
    south_aircraft: list[dict[str, Any]]

    inter_agent_buffer: dict[str, Any]

    active_anomalies: list[dict[str, Any]]
