"""Renders a reasoning node's structured final_instruction into FAA phraseology text.

This is the Phase 4 hybrid integration point: the reasoning LLM (Claude Sonnet 5) decides
WHAT to instruct and calls finalize_instruction with structured fields — it never writes the
radio call itself. This node turns that structured decision into text via a pluggable
PhraseologyBackend. Today that's TemplatePhraseologyBackend, a deterministic formatter with
no model call at all; once Phase 4 produces a fine-tuned checkpoint, swap in a backend that
calls it instead. Nothing about the reasoning node, the coordination protocol, or the graph's
routing needs to change for that swap — only which backend build_graph() passes in here.
"""

from typing import Any, Callable, Coroutine, Protocol

from langchain_core.messages import AIMessage

from state import MultiAgentATCState


class PhraseologyBackend(Protocol):
    async def generate(self, callsign: str, components: list[dict[str, Any]]) -> str: ...


def _format_heading_digits(value: float) -> str:
    degrees = int(round(value)) % 360
    return "-".join(str(degrees).zfill(3))


def _format_component(component: dict[str, Any]) -> str:
    command_type = component["command_type"]
    value = component["value"]

    if command_type == "HEADING_CHANGE":
        direction = "left" if (component.get("direction") or "").upper() == "LEFT" else "right"
        return f"turn {direction} heading {_format_heading_digits(value)}"
    if command_type == "ALTITUDE_CHANGE":
        return f"maintain {int(round(value)):,} feet"
    if command_type == "SPEED_CHANGE":
        return f"maintain {int(round(value))} knots"
    return f"{command_type.lower().replace('_', ' ')} {value}"


class TemplatePhraseologyBackend:
    """Deterministic string formatting, no model call — matches README.md's own guardrail
    that spatial/numeric decisions shouldn't be left to an LLM's judgment, extended here to
    the mechanical act of rendering already-decided values into standard phrasing. Good
    enough to prove the graph integration end-to-end before any fine-tuned model exists, and
    a defensible permanent fallback afterward for exactly the cases (three fixed command
    types, fixed vocabulary) where a template is more reliable than generation."""

    async def generate(self, callsign: str, components: list[dict[str, Any]]) -> str:
        clauses = [_format_component(c) for c in components]
        return f"{callsign}, LAX Tower, {', '.join(clauses)}."


def make_phraseology_generator(
    backend: PhraseologyBackend,
) -> Callable[[MultiAgentATCState], Coroutine[Any, Any, dict]]:
    async def phraseology_generator(state: MultiAgentATCState) -> dict:
        instruction = state["final_instruction"]
        text = await backend.generate(instruction["callsign"], instruction["components"])
        if instruction.get("read_back_required", True):
            text += " Read back all restrictions."

        return {
            "messages": [AIMessage(content=text)],
            "final_instruction": None,
        }

    return phraseology_generator
