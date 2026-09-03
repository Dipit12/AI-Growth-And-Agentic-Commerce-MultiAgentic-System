"""Router node — classifies intent and picks the next node. Layer 2 (agents). Read-only: never
touches the cart or money. See app/agents/prompts/router.md for the classification prompt.
"""

from app.agents.llm import LLMCallFn, default_llm_call, format_conversation
from app.agents.prompts.loader import load_prompt
from app.agents.state import AgentState, Intent
from app.audit.logger import AuditLogger
from app.audit.models import Actor
from app.utils.ids import to_uuid

VALID_INTENTS: set[Intent] = {"discover", "recommend", "cart", "checkout", "support"}


def _parse_intent(raw: str) -> Intent:
    cleaned = raw.strip().lower().strip(".!")
    if cleaned in VALID_INTENTS:
        return cleaned  # type: ignore[return-value]
    for intent in VALID_INTENTS:
        if intent in cleaned:
            return intent  # type: ignore[return-value]
    return "support"


async def router_node(
    state: AgentState,
    *,
    audit: AuditLogger | None = None,
    llm_call: LLMCallFn | None = None,
) -> dict[str, Intent]:
    llm_call = llm_call or default_llm_call

    prompt = load_prompt("router").format(conversation=format_conversation(state["messages"]))
    raw_response = await llm_call(prompt)
    intent = _parse_intent(raw_response)

    if audit is not None:
        await audit.log_agent_event(
            actor=Actor.ROUTER,
            event_type="agent.router.llm_call",
            trace_id=to_uuid(state["trace_id"]),
            session_id=to_uuid(state["session_id"]),
            payload={"raw_response": raw_response, "parsed_intent": intent},
        )

    return {"current_intent": intent}
