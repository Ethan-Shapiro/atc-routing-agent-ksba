"""LangGraph wiring: routing logic and edges for the KSBA 4-role sequential handoff design.

Replaces the LAX-era 2-agent (NORTH_TOWER/SOUTH_TOWER) graph. Each of the 4 roles
(CLEARANCE/GROUND/TOWER/APPROACH) gets its own reasoning node from the same
make_reasoning_node factory used for both of the old design's agents. Unlike that design,
there's no single fixed "next" role: a plain handoff (advance_to_next_role) targets an
EXPLICIT role picked by the LLM and does not re-enter another reasoning node within this
graph run at all — see nodes/handoff_handler.py's docstring for why. The only round-trip
within a single run is Tower's deterministic runway-conflict check, which returns to the
SAME role node that requested it.
"""

import asyncpg
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from nodes.handoff_handler import make_handoff_handler
from nodes.observation_builder import make_observation_builder
from nodes.phraseology_generator import (
    PhraseologyBackend,
    TemplatePhraseologyBackend,
    make_phraseology_generator,
)
from nodes.reasoning_engine import (
    ADVANCE_TO_NEXT_ROLE_TOOL_NAME,
    CHECK_RUNWAY_CONFLICT_TOOL_NAME,
    FINALIZE_INSTRUCTION_TOOL_NAME,
    make_reasoning_node,
)
from roles import ROLE_NAMES
from state import MultiRoleATCState

# Tool names the reasoning node intercepts inline (see reasoning_engine.py) rather than
# routing to tool_executor — anything else in a response's tool_calls is a real MCP call.
_INLINE_HANDLED_TOOL_NAMES = {
    ADVANCE_TO_NEXT_ROLE_TOOL_NAME,
    FINALIZE_INSTRUCTION_TOOL_NAME,
    CHECK_RUNWAY_CONFLICT_TOOL_NAME,
}

_NODE_NAME_BY_ROLE = {role: f"{role.lower()}_reasoning" for role in ROLE_NAMES}


def _entry_router(state: MultiRoleATCState) -> str:
    """Two ways into the graph: an explicit requested role (main.py's manual
    /trigger/{role}/{icao24} endpoint sets active_role before this runs), or — for the
    anomaly-driven LISTEN/NOTIFY path — whichever role's aircraft list contains the
    triggering anomaly's aircraft."""
    requested_role = state.get("active_role")
    if requested_role in _NODE_NAME_BY_ROLE:
        return _NODE_NAME_BY_ROLE[requested_role]

    anomalies = state.get("active_anomalies") or []
    if not anomalies:
        return END

    target_icao24 = anomalies[0]["aircraft_icao24_1"]
    aircraft_by_role = state.get("aircraft_by_role") or {}
    for role, node_name in _NODE_NAME_BY_ROLE.items():
        ids = {a["icao24"] for a in aircraft_by_role.get(role, [])}
        if target_icao24 in ids:
            return node_name
    # Aircraft isn't in any role's current observation (e.g. it already left) — nothing
    # actionable this turn.
    return END


def _reasoning_router(state: MultiRoleATCState) -> str:
    last_message = state["messages"][-1]

    tool_calls = getattr(last_message, "tool_calls", None) or []
    # advance_to_next_role, finalize_instruction, and check_runway_conflict are all handled
    # inline by the reasoning node, never routed to tool_executor — only real MCP calls
    # (query_radar/query_faa_rules) reach it here.
    mcp_tool_calls = [tc for tc in tool_calls if tc["name"] not in _INLINE_HANDLED_TOOL_NAMES]
    if mcp_tool_calls:
        return "tool_executor"

    # A pending handoff_buffer entry (either kind) needs to be resolved before this turn is
    # done — checked before final_instruction because a CONFLICT_CHECK is a prerequisite for
    # ever reaching a finalized decision (reasoning_engine.py defers finalize/advance calls
    # made in the same turn as a conflict check, so the two states can't both be pending at
    # once for the same role).
    buffer = state.get("handoff_buffer") or {}
    if buffer.get("status") == "PENDING":
        return "handoff_handler"

    if state.get("final_instruction"):
        return "phraseology_generator"

    return END


def _route_back_to_active_role(state: MultiRoleATCState) -> str:
    return _NODE_NAME_BY_ROLE[state["active_role"]]


def _route_after_handoff(state: MultiRoleATCState) -> str:
    buffer = state["handoff_buffer"]
    if buffer["kind"] == "CONFLICT_CHECK":
        # Return to the same role so it can act on the ACK/COUNTER_PROPOSAL verdict.
        return _NODE_NAME_BY_ROLE[state["active_role"]]
    # kind == "HANDOFF": does not re-enter the target role's node (see module docstring) —
    # only continues to phraseology_generator if this turn ALSO finalized an instruction.
    if state.get("final_instruction"):
        return "phraseology_generator"
    return END


def build_graph(
    llm: BaseChatModel,
    mcp_tools: list[StructuredTool],
    pool: asyncpg.Pool,
    airport_lat: float,
    airport_lon: float,
    phraseology_backend: PhraseologyBackend | None = None,
):
    graph = StateGraph(MultiRoleATCState)

    graph.add_node(
        "observation_builder", make_observation_builder(pool, airport_lat=airport_lat, airport_lon=airport_lon)
    )
    for role in ROLE_NAMES:
        graph.add_node(_NODE_NAME_BY_ROLE[role], make_reasoning_node(role, llm, mcp_tools))
    graph.add_node("tool_executor", ToolNode(mcp_tools))
    graph.add_node("handoff_handler", make_handoff_handler(pool))
    graph.add_node(
        "phraseology_generator",
        make_phraseology_generator(phraseology_backend or TemplatePhraseologyBackend()),
    )

    graph.set_entry_point("observation_builder")
    graph.add_conditional_edges(
        "observation_builder",
        _entry_router,
        {**{node: node for node in _NODE_NAME_BY_ROLE.values()}, END: END},
    )

    for node in _NODE_NAME_BY_ROLE.values():
        graph.add_conditional_edges(
            node,
            _reasoning_router,
            {
                "tool_executor": "tool_executor",
                "handoff_handler": "handoff_handler",
                "phraseology_generator": "phraseology_generator",
                END: END,
            },
        )

    graph.add_conditional_edges(
        "tool_executor",
        _route_back_to_active_role,
        {node: node for node in _NODE_NAME_BY_ROLE.values()},
    )
    graph.add_conditional_edges(
        "handoff_handler",
        _route_after_handoff,
        {**{node: node for node in _NODE_NAME_BY_ROLE.values()}, "phraseology_generator": "phraseology_generator", END: END},
    )
    # Terminal: phraseology has been generated, the turn is done.
    graph.add_edge("phraseology_generator", END)

    return graph.compile()
