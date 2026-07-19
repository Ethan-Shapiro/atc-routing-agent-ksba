"""Renders a reasoning node's structured final_instruction into FAA phraseology text.

This is the Phase 4 hybrid integration point: the reasoning LLM (Claude Sonnet 5) decides
WHAT to instruct and calls finalize_instruction with structured fields — it never writes the
radio call itself. This node turns that structured decision into text via a pluggable
PhraseologyBackend. Today that's TemplatePhraseologyBackend, a deterministic formatter with
no model call at all; once Phase 4 produces a fine-tuned checkpoint, swap in a backend that
calls it instead. Nothing about the reasoning node, the handoff protocol, or the graph's
routing needs to change for that swap — only which backend build_graph() passes in here.

Generalized for the KSBA 4-role rescope: the old 2-agent design's InstructionComponent was a
closed 3-type numeric action space (HEADING_CHANGE/ALTITUDE_CHANGE/SPEED_CHANGE) sized for
airborne conflict-avoidance only. KSBA's 4 roles need a much wider vocabulary (CRAFT clearance
elements, taxi/hold-short instructions, takeoff/landing clearances, frequency handoffs), so
command_type/value are now free-form strings (see reasoning_engine.py's InstructionComponent)
and the template needs the issuing role to pick the right facility name.
"""

from typing import Any, Callable, Coroutine, Protocol

from langchain_core.messages import AIMessage

from roles import ROLE_FACILITY_NAME
from state import MultiRoleATCState


class PhraseologyBackend(Protocol):
    async def generate(self, role_name: str, callsign: str, components: list[dict[str, Any]]) -> str: ...


def _format_component(component: dict[str, Any]) -> str:
    command_type = component["command_type"]
    value = component["value"]
    return f"{command_type.lower().replace('_', ' ')} {value}"


class TemplatePhraseologyBackend:
    """Deterministic string formatting, no model call — matches README.md's own guardrail
    that spatial/numeric decisions shouldn't be left to an LLM's judgment, extended here to
    the mechanical act of rendering already-decided values into standard phrasing. Good
    enough to prove the graph integration end-to-end before any fine-tuned model exists.

    Unlike the 2-agent design's fixed-vocabulary formatter, command_type/value are now
    free-form (see reasoning_engine.py), so this produces serviceable but generic phrasing
    ("clearance limit KSBA", "runway assignment 7") rather than idiomatic ATC phrasing
    ("cleared to Santa Barbara Municipal"). That gap — generic-but-correct structured
    rendering vs. idiomatic radio phraseology — is exactly what a fine-tuned phraseology
    model would close; the template's job is only to prove the pipeline end-to-end."""

    async def generate(self, role_name: str, callsign: str, components: list[dict[str, Any]]) -> str:
        clauses = [_format_component(c) for c in components]
        facility = ROLE_FACILITY_NAME.get(role_name, role_name)
        return f"{callsign}, {facility}, {', '.join(clauses)}."


def make_phraseology_generator(
    backend: PhraseologyBackend,
) -> Callable[[MultiRoleATCState], Coroutine[Any, Any, dict]]:
    async def phraseology_generator(state: MultiRoleATCState) -> dict:
        instruction = state["final_instruction"]
        text = await backend.generate(
            instruction["role_name"], instruction["callsign"], instruction["components"]
        )
        if instruction.get("read_back_required", True):
            text += " Read back all restrictions."

        return {
            "messages": [AIMessage(content=text)],
            "final_instruction": None,
        }

    return phraseology_generator
