"""Support / fallback node — catches intents the other nodes couldn't handle. Layer 2 (agents).
Always a graceful terminal node: never loops back to the router, never touches money.
"""

from app.agents.llm import LLMCallFn, default_llm_call, format_conversation
from app.agents.prompts.loader import load_prompt
from app.agents.state import AgentState
from app.audit.logger import AuditLogger
from app.audit.models import Actor
from app.utils.ids import to_uuid


async def support_node(
    state: AgentState,
    *,
    audit: AuditLogger | None = None,
    llm_call: LLMCallFn | None = None,
) -> dict[str, object]:
    llm_call = llm_call or default_llm_call

    prompt = load_prompt("support").format(conversation=format_conversation(state["messages"]))
    message = await llm_call(prompt)

    if audit is not None:
        await audit.log_agent_event(
            actor=Actor.SUPPORT,
            event_type="agent.support.fallback",
            trace_id=to_uuid(state["trace_id"]),
            session_id=to_uuid(state["session_id"]),
        )

    return {"final_response": message}
