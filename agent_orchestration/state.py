"""MultiAgentATCState schema, based on multi-agent_orchestration_rf_layer.md.

north_aircraft/south_aircraft are populated by nodes/observation_builder.py via a direct
ST_Contains(runway_complex.boundary, aircraft.geom) query against the table Phase 1 seeds
(data_pipeline/sql/004_seed_reference_data.sql).

final_instruction is an extension beyond the original spec document, added for the Phase 4
hybrid integration: a reasoning node populates it (via the finalize_instruction tool, see
nodes/reasoning_engine.py) once it has decided on a concrete ATC action, and
nodes/phraseology_generator.py renders it into FAA phraseology text as a separate step —
keeping "decide what to do" (the reasoning LLM) and "phrase it" (eventually the fine-tuned
model) as distinct, independently swappable stages.
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

    final_instruction: dict[str, Any] | None
