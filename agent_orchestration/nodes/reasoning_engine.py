"""4-role KSBA reasoning nodes: Clearance Delivery, Ground, Tower, Approach.

Replaces the LAX-era North/South Tower design. Rules below are adapted directly from
ksba_prototype/agents.py's SYSTEM_PROMPTS, which validated well against 9 isolated scenarios
and 2 full handoff chains (see ksba_prototype/scenarios.py, chains.py). The key behavioral
change from that prototype: instead of receiving a hand-authored "Current Airspace State"
JSON blob in the user message, each node is bound to the real query_radar/query_faa_rules MCP
tools and is expected to call them for live aircraft state and rule citations, rather than
trusting whatever context happened to be in the triggering message.

Two more tools beyond the MCP pair:
  - finalize_instruction — unchanged mechanism from the 2-agent design (see
    nodes/phraseology_generator.py), generalized to a free-form command_type/value schema
    since KSBA's 4 roles need a much wider instruction vocabulary (CRAFT clearance elements,
    taxi/hold-short instructions, takeoff/landing clearance, frequency handoffs) than the old
    3-type airborne-maneuver-only action space.
  - advance_to_next_role — replaces the 2-agent design's request_coordination. A role hands
    an aircraft to an EXPLICIT target_role (not an implicit "next" lookup) since KSBA's real
    chain runs in two directions: departures go Clearance -> Ground -> Tower -> Approach,
    arrivals go Approach -> Tower -> Ground. This does not re-invoke the target role's
    reasoning node within the same graph run — it writes an audit row (coordination_events)
    and the actual next radio exchange happens via a separate trigger, same as real ATC
    handoffs happen as separate radio calls, not one continuous exchange.
  - check_runway_conflict — Tower-only. A genuine "ask a deterministic system, then act on
    the answer" round-trip (unlike advance_to_next_role): Tower's 2 NM intersection rule is a
    real spatial safety threshold, and per the project's "LLMs cannot do math" guardrail
    (already applied in nodes/handoff_handler.py), that check runs as a real PostGIS distance
    query, not model inference. Tower's turn pauses for the verdict before it can finalize a
    hold-short or takeoff/landing clearance decision.
"""

import json
from typing import Any, Callable, Coroutine

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from roles import ROLE_FACILITY_NAME, ROLE_FREQUENCY_MHZ, ROLE_NAMES
from state import MultiRoleATCState

FINALIZE_INSTRUCTION_TOOL_NAME = "finalize_instruction"
ADVANCE_TO_NEXT_ROLE_TOOL_NAME = "advance_to_next_role"
CHECK_RUNWAY_CONFLICT_TOOL_NAME = "check_runway_conflict"

_RULES_BY_ROLE = {
    "CLEARANCE": """Your objective is to issue IFR route clearances to departing aircraft before they push back from the gate.

Rules:
1. You must follow the CRAFT format perfectly: Clearance Limit, Route, Altitude, Frequency, Transponder.
2. For commercial jets departing Runway 7, assign the standard departure frequency 120.55 (Approach).
3. If an aircraft calls for taxi, deny the request and instruct them to contact Ground on 121.7 — do not issue a taxi clearance yourself.
4. Once you've decided the clearance, call finalize_instruction with one component per CRAFT element (command_type: CLEARANCE_LIMIT / ROUTE / ALTITUDE / DEPARTURE_FREQUENCY / TRANSPONDER), then call advance_to_next_role with target_role='GROUND'.""",
    "GROUND": """Your objective is to safely route aircraft from their parking areas to the active runway thresholds without causing incursions.

Rules:
1. Commercial jets (regional jets, e.g. CRJ700/ERJ) MUST be routed to Runway 7 or 25.
2. General Aviation aircraft (C172, PA28) should be routed to Runway 15L or 15R.
3. You must explicitly instruct aircraft to "hold short" of any intersecting runway during their taxi route, and you MUST demand a readback from the pilot.
4. Call finalize_instruction with the taxi routing (command_type: RUNWAY_ASSIGNMENT, TAXI_ROUTE, HOLD_SHORT as separate components), then call advance_to_next_role with target_role='TOWER' once the aircraft is holding short of its departure runway — not before.""",
    "TOWER": """Your objective is to safely sequence takeoffs and landings.

CRITICAL INTERSECTION RULES:
1. Runways 15L, 15R, 33L, and 33R physically intersect Runway 7/25 (see the `runway` table's intersects_with data).
2. You MUST NEVER clear an aircraft for takeoff or landing on any 15/33 runway if a commercial aircraft is within 2 nautical miles of the threshold on final approach for Runway 7 or 25. Call check_runway_conflict BEFORE finalizing a takeoff or landing clearance on a 15/33 runway to verify this deterministically — do not estimate the distance yourself.
3. If an aircraft is on the runway and a conflict occurs, immediately issue a "go-around" command via finalize_instruction (command_type: GO_AROUND).

General Rules:
1. Use standard FAA Order JO 7110.65 phraseology (e.g., "Cleared for takeoff", "Cleared to land", "Hold short") — use query_faa_rules if you need to confirm exact wording.
2. Once an aircraft departs and passes 1,000 feet, call finalize_instruction (command_type: CONTACT_FREQUENCY, value='120.55') then advance_to_next_role with target_role='APPROACH'.
3. For an arrival handed to you by Approach, once the aircraft has landed and vacated the runway, call finalize_instruction (command_type: CONTACT_FREQUENCY, value='121.7') then advance_to_next_role with target_role='GROUND'.
4. If you must withhold a clearance due to a conflict, call finalize_instruction with the hold-short instruction and a traffic advisory instead — never leave a request unanswered.""",
    "APPROACH": """Your objective is to sequence incoming IFR traffic from the en-route phase into a clean final approach line for KSBA, and to accept outbound aircraft on climb-out from Tower.

Rules:
1. You must maintain 3 miles of lateral separation or 1,000 feet of vertical separation between all targets at all times.
2. Speed control is your primary tool. You may instruct faster jets to slow down (command_type: SPEED_CHANGE) to avoid overtaking slower propeller aircraft — vectors/altitude changes are secondary tools, not the default.
3. Once an aircraft is established on the localizer for their assigned runway and is 5 miles out, call finalize_instruction (command_type: CONTACT_FREQUENCY, value='119.7') then advance_to_next_role with target_role='TOWER'.
4. For a departure checking in from Tower on climb-out, acknowledge radar contact and issue further climb/routing via finalize_instruction — this is the terminal role in the departure chain, no further handoff needed.""",
}

_SYSTEM_PROMPT_TEMPLATE = """You are the {facility_name} controller at Santa Barbara Municipal Airport (KSBA), operating on frequency {frequency}.

{rules}

Grounding rules (apply to every role):
- Use `query_radar` to check an aircraft's current state before acting on it, rather than trusting only what's in this message.
- Before relying on an FAA separation/wake-turbulence/phraseology rule you're not certain of, use `query_faa_rules` to confirm it — you cannot reliably do spatial or regulatory recall from memory.
- Respond with the exact radio phraseology you would transmit as plain text alongside your tool calls — no narration, no explanation of what you're doing, just what you would actually say over the radio, driven by what you pass to `finalize_instruction`.
- If no action is needed yet, say so briefly in plain text and stop — do not call `finalize_instruction`.

Aircraft currently in your jurisdiction:
{aircraft_json}

Active anomalies (all roles):
{anomalies_json}

Current handoff/conflict-check status (empty if none pending or resolved this turn):
{handoff_json}
"""


class InstructionComponent(BaseModel):
    command_type: str = Field(
        description="Free-form label for this instruction element — e.g. CLEARANCE_LIMIT, "
        "ROUTE, ALTITUDE, DEPARTURE_FREQUENCY, TRANSPONDER, RUNWAY_ASSIGNMENT, TAXI_ROUTE, "
        "HOLD_SHORT, TAKEOFF_CLEARANCE, LANDING_CLEARANCE, GO_AROUND, CONTACT_FREQUENCY, "
        "HEADING_CHANGE, ALTITUDE_CHANGE, SPEED_CHANGE, TRAFFIC_ADVISORY — whatever this "
        "role's rules call for."
    )
    value: str = Field(
        description="The instruction detail as spoken text or a value, e.g. 'KSBA', "
        "'SBA V25 LAX', '16000', '120.55', '4271', 'via Alpha, hold short Runway 15R/33L'."
    )


class FinalizeInstructionInput(BaseModel):
    aircraft_id: str = Field(description="ICAO24 hex address of the aircraft being instructed")
    callsign: str = Field(description="The aircraft's callsign as spoken, e.g. 'SkyWest 2450'")
    components: list[InstructionComponent] = Field(
        description="One or more instruction components to issue together in a single radio call"
    )
    read_back_required: bool = Field(
        default=True, description="Whether the pilot must read back the instruction"
    )


def _build_finalize_instruction_tool() -> StructuredTool:
    async def _noop(**kwargs) -> str:
        return "handled by reasoning node"

    return StructuredTool.from_function(
        name=FINALIZE_INSTRUCTION_TOOL_NAME,
        description=(
            "Finalize a concrete ATC instruction for an aircraft in your jurisdiction. Call "
            "this once you have decided what to instruct — do not write the radio phraseology "
            "yourself, a separate rendering step handles that from your structured input."
        ),
        args_schema=FinalizeInstructionInput,
        coroutine=_noop,
    )


class AdvanceToNextRoleInput(BaseModel):
    target_role: str = Field(description=f"One of {list(ROLE_NAMES)} — the role to hand this aircraft to next")
    aircraft_id: str = Field(description="ICAO24 hex address of the aircraft being handed off")
    reason: str = Field(description="Brief note on why the handoff is happening now")


def _build_advance_to_next_role_tool() -> StructuredTool:
    async def _noop(**kwargs) -> str:
        return "handled by reasoning node"

    return StructuredTool.from_function(
        name=ADVANCE_TO_NEXT_ROLE_TOOL_NAME,
        description=(
            "Hand an aircraft off to the next controller role. Does not require a response — "
            "it records the handoff for audit purposes. Departures typically flow "
            "CLEARANCE -> GROUND -> TOWER -> APPROACH; arrivals flow APPROACH -> TOWER -> GROUND."
        ),
        args_schema=AdvanceToNextRoleInput,
        coroutine=_noop,
    )


class CheckRunwayConflictInput(BaseModel):
    aircraft_id: str = Field(description="ICAO24 hex address of the aircraft awaiting the runway")
    runway_id: str = Field(description="The runway this aircraft is holding short of or landing/departing on, e.g. '15R'")


def _build_check_runway_conflict_tool() -> StructuredTool:
    async def _noop(**kwargs) -> str:
        return "handled by reasoning node"

    return StructuredTool.from_function(
        name=CHECK_RUNWAY_CONFLICT_TOOL_NAME,
        description=(
            "Deterministically check whether any commercial aircraft is within 2 NM of the "
            "threshold of a runway that intersects the given runway_id. Call this before "
            "clearing a 15/33 takeoff or landing — do not estimate the conflict distance "
            "yourself."
        ),
        args_schema=CheckRunwayConflictInput,
        coroutine=_noop,
    )


def make_reasoning_node(
    role_name: str,
    llm: BaseChatModel,
    mcp_tools: list[StructuredTool],
) -> Callable[[MultiRoleATCState], Coroutine[Any, Any, dict]]:
    finalize_tool = _build_finalize_instruction_tool()
    advance_tool = _build_advance_to_next_role_tool()
    local_tools = [finalize_tool, advance_tool]
    if role_name == "TOWER":
        local_tools.append(_build_check_runway_conflict_tool())
    llm_with_tools = llm.bind_tools([*mcp_tools, *local_tools])

    async def reasoning_node(state: MultiRoleATCState) -> dict:
        system = SystemMessage(
            content=_SYSTEM_PROMPT_TEMPLATE.format(
                facility_name=ROLE_FACILITY_NAME[role_name],
                frequency=ROLE_FREQUENCY_MHZ[role_name],
                rules=_RULES_BY_ROLE[role_name],
                aircraft_json=json.dumps(state.get("aircraft_by_role", {}).get(role_name, []), default=str),
                anomalies_json=json.dumps(state.get("active_anomalies", []), default=str),
                handoff_json=json.dumps(state.get("handoff_buffer") or {}, default=str),
            )
        )
        response: AIMessage = await llm_with_tools.ainvoke([system, *state["messages"]])

        update: dict[str, Any] = {"messages": [response], "active_role": role_name}

        all_calls = response.tool_calls or []
        conflict_calls = [tc for tc in all_calls if tc["name"] == CHECK_RUNWAY_CONFLICT_TOOL_NAME]
        finalize_calls = [tc for tc in all_calls if tc["name"] == FINALIZE_INSTRUCTION_TOOL_NAME]
        advance_calls = [tc for tc in all_calls if tc["name"] == ADVANCE_TO_NEXT_ROLE_TOOL_NAME]

        # Same pattern as the 2-agent design: these three are always handled inline (they
        # mutate graph state, not an external system), never routed to tool_executor. If the
        # model also requested a real MCP tool in the same turn, it gets a deferred
        # placeholder rather than actually executing — avoids the ordering complexity of a
        # tool_executor round-trip happening in between an inline decision and its routing
        # consequence (e.g. a stale final_instruction/handoff_buffer sitting in state across
        # an extra LLM turn). The model can still call query_radar/query_faa_rules freely on
        # a turn where it ISN'T also finalizing/advancing/checking a conflict.
        #
        # Priority when more than one appears in the same turn: a pending conflict check
        # wins — Tower can't finalize a takeoff/landing decision until it has the
        # deterministic verdict, so a finalize/advance call made in the same turn as
        # check_runway_conflict is premature.
        if conflict_calls:
            call = conflict_calls[0]
            update["handoff_buffer"] = {
                "kind": "CONFLICT_CHECK",
                "status": "PENDING",
                "origin_role": role_name,
                "aircraft_id": call["args"]["aircraft_id"],
                "runway_id": call["args"]["runway_id"],
            }
            tool_messages = [
                ToolMessage(content="Runway conflict check requested, awaiting verdict.", tool_call_id=call["id"])
            ]
            for tc in all_calls:
                if tc["id"] != call["id"]:
                    tool_messages.append(
                        ToolMessage(content="Deferred — conflict check takes precedence this turn.", tool_call_id=tc["id"])
                    )
            update["messages"] = [response, *tool_messages]
            return update

        if not finalize_calls and not advance_calls:
            # Neither local tool was called — either plain text (no action needed) or pure
            # MCP tool calls to gather info. Let those reach tool_executor normally.
            return update

        tool_messages = []
        if finalize_calls:
            call = finalize_calls[0]
            args = call["args"]
            update["final_instruction"] = {
                "role_name": role_name,
                "aircraft_id": args["aircraft_id"],
                "callsign": args["callsign"],
                "components": args["components"],
                "read_back_required": args.get("read_back_required", True),
            }
            tool_messages.append(
                ToolMessage(content="Instruction finalized, phraseology being generated.", tool_call_id=call["id"])
            )

        if advance_calls:
            call = advance_calls[0]
            args = call["args"]
            update["handoff_buffer"] = {
                "kind": "HANDOFF",
                "status": "PENDING",
                "origin_role": role_name,
                "target_role": args["target_role"],
                "aircraft_id": args["aircraft_id"],
                "reason": args["reason"],
            }
            tool_messages.append(
                ToolMessage(content=f"Handoff to {args['target_role']} recorded.", tool_call_id=call["id"])
            )

        handled_ids = {tc["id"] for tc in finalize_calls[:1] + advance_calls[:1]}
        for tc in all_calls:
            if tc["id"] not in handled_ids:
                tool_messages.append(
                    ToolMessage(content="Deferred — finalized this turn takes precedence.", tool_call_id=tc["id"])
                )

        update["messages"] = [response, *tool_messages]
        return update

    return reasoning_node
