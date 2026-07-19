"""LangGraph wiring: routing logic and edges, per multi-agent_orchestration_rf_layer.md.

Design note on the START fan-out: the spec's ASCII diagram shows arrows from START into
both North and South Reasoning converging on one Conditional Router. Taken literally
(both branches firing every turn) that's inconsistent with the router's own logic, which
reads a single `state["active_agent"]` and `state["messages"][-1]` — values that only make
sense for one agent's turn. This graph instead treats `active_agent` as "who currently holds
execution focus" (its own field description) — a turn-based single-active-agent loop, not a
parallel fan-out — since that's the interpretation the router code actually supports. An
entry router (using observation_builder's north/south classification) picks the one agent
whose jurisdiction the triggering anomaly falls in.
"""

import asyncpg
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from nodes.inter_agent_comm_handler import make_inter_agent_comm_handler
from nodes.observation_builder import make_observation_builder
from nodes.phraseology_generator import (
    PhraseologyBackend,
    TemplatePhraseologyBackend,
    make_phraseology_generator,
)
from nodes.reasoning_engine import (
    FINALIZE_INSTRUCTION_TOOL_NAME,
    REQUEST_COORDINATION_TOOL_NAME,
    make_reasoning_node,
)
from state import MultiAgentATCState

# Tool names the reasoning node intercepts inline (see reasoning_engine.py) rather than
# routing to tool_executor — anything else in a response's tool_calls is a real MCP call.
_INLINE_HANDLED_TOOL_NAMES = {REQUEST_COORDINATION_TOOL_NAME, FINALIZE_INSTRUCTION_TOOL_NAME}


def _entry_router(state: MultiAgentATCState) -> str:
    """Picks which agent's turn it is based on the triggering anomaly's aircraft."""
    anomalies = state.get("active_anomalies") or []
    if not anomalies:
        return END

    target_icao24 = anomalies[0]["aircraft_icao24_1"]
    north_ids = {a["icao24"] for a in state.get("north_aircraft", [])}
    south_ids = {a["icao24"] for a in state.get("south_aircraft", [])}

    if target_icao24 in north_ids:
        return "north_reasoning"
    if target_icao24 in south_ids:
        return "south_reasoning"
    # Aircraft isn't in either complex's current observation (e.g. it already left) —
    # nothing actionable this turn.
    return END


def _multi_agent_router(state: MultiAgentATCState) -> str:
    """Verbatim logic from multi-agent_orchestration_rf_layer.md's multi_agent_router,
    adapted for LangGraph's conditional-edge return-a-node-name convention."""
    last_message = state["messages"][-1]

    tool_calls = getattr(last_message, "tool_calls", None) or []
    # request_coordination and finalize_instruction are both handled inline by the reasoning
    # node, never routed to tool_executor — only real MCP calls (query_radar/query_faa_rules)
    # reach it here.
    mcp_tool_calls = [tc for tc in tool_calls if tc["name"] not in _INLINE_HANDLED_TOOL_NAMES]
    if mcp_tool_calls:
        return "tool_executor"

    # A finalized instruction takes priority over END: the reasoning node has decided on a
    # concrete action and needs it rendered into phraseology before this turn is done.
    if state.get("final_instruction"):
        return "phraseology_generator"

    # requires_coordination is the sole guard, and it's reliable: inter_agent_comm_handler
    # clears it to False on resolution. (An earlier version additionally checked
    # `active_agent != "COORDINATOR"` — but reasoning_node unconditionally overwrites
    # active_agent to its own name on every call, silently clobbering that guard before the
    # router ever saw it, which caused an infinite reasoning <-> comm_handler loop.)
    buffer = state.get("inter_agent_buffer") or {}
    if buffer.get("requires_coordination"):
        return "inter_agent_comm_handler"

    return END


def _route_back_to_active_agent(state: MultiAgentATCState) -> str:
    return "north_reasoning" if state["active_agent"] == "NORTH_TOWER" else "south_reasoning"


def _route_back_to_origin_agent(state: MultiAgentATCState) -> str:
    origin = state["inter_agent_buffer"]["origin_agent"]
    return "north_reasoning" if origin == "NORTH_TOWER" else "south_reasoning"


def build_graph(
    llm: BaseChatModel,
    mcp_tools: list[StructuredTool],
    pool: asyncpg.Pool,
    phraseology_backend: PhraseologyBackend | None = None,
):
    graph = StateGraph(MultiAgentATCState)

    graph.add_node("observation_builder", make_observation_builder(pool))
    graph.add_node("north_reasoning", make_reasoning_node("NORTH_TOWER", llm, mcp_tools))
    graph.add_node("south_reasoning", make_reasoning_node("SOUTH_TOWER", llm, mcp_tools))
    graph.add_node("tool_executor", ToolNode(mcp_tools))
    graph.add_node("inter_agent_comm_handler", make_inter_agent_comm_handler(pool))
    graph.add_node(
        "phraseology_generator",
        make_phraseology_generator(phraseology_backend or TemplatePhraseologyBackend()),
    )

    graph.set_entry_point("observation_builder")
    graph.add_conditional_edges(
        "observation_builder",
        _entry_router,
        {"north_reasoning": "north_reasoning", "south_reasoning": "south_reasoning", END: END},
    )

    for node in ("north_reasoning", "south_reasoning"):
        graph.add_conditional_edges(
            node,
            _multi_agent_router,
            {
                "tool_executor": "tool_executor",
                "phraseology_generator": "phraseology_generator",
                "inter_agent_comm_handler": "inter_agent_comm_handler",
                END: END,
            },
        )

    graph.add_conditional_edges(
        "tool_executor",
        _route_back_to_active_agent,
        {"north_reasoning": "north_reasoning", "south_reasoning": "south_reasoning"},
    )
    graph.add_conditional_edges(
        "inter_agent_comm_handler",
        _route_back_to_origin_agent,
        {"north_reasoning": "north_reasoning", "south_reasoning": "south_reasoning"},
    )
    # Terminal: phraseology has been generated, the turn is done.
    graph.add_edge("phraseology_generator", END)

    return graph.compile()
