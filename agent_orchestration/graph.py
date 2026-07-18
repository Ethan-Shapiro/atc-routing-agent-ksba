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
from nodes.reasoning_engine import REQUEST_COORDINATION_TOOL_NAME, make_reasoning_node
from state import MultiAgentATCState


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
    # request_coordination is handled inline by the reasoning node, never routed to
    # tool_executor — only MCP tool calls (query_radar/query_faa_rules) reach it here.
    mcp_tool_calls = [tc for tc in tool_calls if tc["name"] != REQUEST_COORDINATION_TOOL_NAME]
    if mcp_tool_calls:
        return "tool_executor"

    buffer = state.get("inter_agent_buffer") or {}
    if buffer.get("requires_coordination") and state["active_agent"] != "COORDINATOR":
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
):
    graph = StateGraph(MultiAgentATCState)

    graph.add_node("observation_builder", make_observation_builder(pool))
    graph.add_node("north_reasoning", make_reasoning_node("NORTH_TOWER", llm, mcp_tools))
    graph.add_node("south_reasoning", make_reasoning_node("SOUTH_TOWER", llm, mcp_tools))
    graph.add_node("tool_executor", ToolNode(mcp_tools))
    graph.add_node("inter_agent_comm_handler", make_inter_agent_comm_handler(pool))

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

    return graph.compile()
