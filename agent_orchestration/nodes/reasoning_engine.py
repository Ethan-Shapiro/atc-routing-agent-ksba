"""North/South Tower reasoning nodes.

Strict agent boundaries (README.md guardrail): a reasoning node never issues a command to an
aircraft outside its own jurisdiction. When an anomaly requires a cross-boundary action, the
agent calls the `request_coordination` tool instead of acting directly — this populates
inter_agent_buffer with the JSON contract from multi-agent_orchestration_rf_layer.md and hands
control to inter_agent_comm_handler via the conditional router, requiring an ACK from the
opposing agent before any phraseology is generated.
"""

import json
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from state import MultiAgentATCState

REQUEST_COORDINATION_TOOL_NAME = "request_coordination"

_SYSTEM_PROMPT_TEMPLATE = """You are the {agent_name} controller at LAX, responsible for \
runways and taxiways in the {agent_name} complex only. You monitor the aircraft listed below \
and any active safety anomalies, and decide whether an intervention is needed.

Hard rules:
- You may only reason about and act on aircraft in YOUR complex ({agent_name}). You must \
never issue an instruction to an aircraft belonging to the other complex.
- If an anomaly or a planned action would require an aircraft to cross into the other \
complex's jurisdiction (e.g. a taxi crossing, a missed approach into the other complex's \
departure path), you must call `{tool_name}` instead of acting directly. Do not generate \
phraseology for a cross-boundary action until that request has been acknowledged.
- Before deciding on a separation or wake-turbulence action, use `query_faa_rules` to confirm \
the applicable minimums rather than guessing — you cannot reliably do spatial/regulatory \
math from memory.
- Use `query_radar` to check an aircraft's current state before acting on it.
- If no action is needed, say so briefly and stop — do not narrate what you are not doing.
- Once you have gathered the information you need and decided on (or explicitly ruled out) \
an action, give your final response as plain text with NO further tool calls. A coordination \
request that has already reached ACK or COUNTER_PROPOSAL status (see below) is resolved —
do not call `{tool_name}` again for the same aircraft; either proceed (on ACK) or state why \
you are holding (on COUNTER_PROPOSAL) and stop.

Current {agent_name} aircraft:
{aircraft_json}

Active anomalies (all complexes):
{anomalies_json}

Current inter-agent coordination status (empty if none pending or resolved this turn):
{coordination_json}
"""


class RequestCoordinationInput(BaseModel):
    target_agent: str = Field(description="'NORTH_TOWER' or 'SOUTH_TOWER' — the other complex")
    aircraft_id: str = Field(description="ICAO24 hex address of the aircraft requiring coordination")
    action_type: str = Field(description="e.g. 'TAXI_CROSSING', 'MISSED_APPROACH_INTO_ADJACENT_COMPLEX'")
    coordinate_threshold: list[float] = Field(description="[lat, lon] of the crossing point")
    estimated_time_crossing: str = Field(description="ISO 8601 timestamp estimate")
    reason: str = Field(description="Brief explanation of why coordination is required")


def _build_request_coordination_tool() -> StructuredTool:
    async def _noop(**kwargs) -> str:
        # Never actually invoked — the reasoning node intercepts this tool call before
        # it would reach a generic tool executor, since it mutates graph state rather
        # than calling an external system. The coroutine exists only so bind_tools()
        # has something schema-valid to attach.
        return "handled by reasoning node"

    return StructuredTool.from_function(
        name=REQUEST_COORDINATION_TOOL_NAME,
        description=(
            "Request coordination from the other tower complex before taking any action "
            "that would affect an aircraft crossing into their jurisdiction. Never act on "
            "a cross-boundary situation directly."
        ),
        args_schema=RequestCoordinationInput,
        coroutine=_noop,
    )


def make_reasoning_node(
    agent_name: str,
    llm: BaseChatModel,
    mcp_tools: list[StructuredTool],
) -> Callable[[MultiAgentATCState], Coroutine[Any, Any, dict]]:
    coordination_tool = _build_request_coordination_tool()
    llm_with_tools = llm.bind_tools([*mcp_tools, coordination_tool])
    aircraft_key = "north_aircraft" if agent_name == "NORTH_TOWER" else "south_aircraft"

    async def reasoning_node(state: MultiAgentATCState) -> dict:
        system = SystemMessage(
            content=_SYSTEM_PROMPT_TEMPLATE.format(
                agent_name=agent_name,
                tool_name=REQUEST_COORDINATION_TOOL_NAME,
                aircraft_json=json.dumps(state.get(aircraft_key, []), default=str),
                anomalies_json=json.dumps(state.get("active_anomalies", []), default=str),
                coordination_json=json.dumps(state.get("inter_agent_buffer") or {}, default=str),
            )
        )
        response: AIMessage = await llm_with_tools.ainvoke([system, *state["messages"]])

        update: dict[str, Any] = {"messages": [response], "active_agent": agent_name}

        all_calls = response.tool_calls or []
        coordination_calls = [tc for tc in all_calls if tc["name"] == REQUEST_COORDINATION_TOOL_NAME]

        if coordination_calls:
            call = coordination_calls[0]
            args = call["args"]
            update["inter_agent_buffer"] = {
                "requires_coordination": True,
                "origin_agent": agent_name,
                "target_agent": args["target_agent"],
                "aircraft_id": args["aircraft_id"],
                "proposed_action": {
                    "type": args["action_type"],
                    "coordinate_threshold": args["coordinate_threshold"],
                    "estimated_time_crossing": args["estimated_time_crossing"],
                },
                "reason": args["reason"],
                "coordination_status": "PENDING",
                "requested_at": datetime.now(timezone.utc).isoformat(),
            }
            # Acknowledge the tool call locally so the message history stays valid for the
            # next LLM turn — this tool is handled here, never sent to tool_executor.
            tool_messages = [
                ToolMessage(
                    content=f"Coordination request recorded, awaiting ACK from {args['target_agent']}.",
                    tool_call_id=call["id"],
                )
            ]
            # Defensive: Anthropic allows parallel tool calls, so the model may have asked
            # for query_radar/query_faa_rules in the SAME turn as request_coordination. Every
            # tool_use block needs exactly one tool_result before the next API call, but this
            # turn is being diverted to inter_agent_comm_handler instead of tool_executor — so
            # answer any other pending calls with a deferred placeholder rather than leaving
            # them unanswered (which would 400 the next call the same way the missing
            # coordination result did).
            for tc in all_calls:
                if tc["name"] != REQUEST_COORDINATION_TOOL_NAME:
                    tool_messages.append(
                        ToolMessage(
                            content="Deferred — coordination request takes precedence this turn.",
                            tool_call_id=tc["id"],
                        )
                    )
            update["messages"] = [response, *tool_messages]

        return update

    return reasoning_node
