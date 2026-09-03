"""Discovery node — semantic catalog search with validated results. Layer 2 (agents). Read-only:
cannot call Razorpay and cannot modify the cart (invariant #1 boundary).
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.llm import LLMCallFn, default_llm_call, format_conversation, last_user_message
from app.agents.prompts.loader import load_prompt
from app.agents.state import AgentState
from app.audit.logger import AuditLogger
from app.audit.models import Actor
from app.db.session import async_session_factory
from app.integrations.vector_store import VectorStore
from app.models.product import Product
from app.utils.ids import to_uuid
from app.utils.llm_json import extract_json


async def discovery_node(
    state: AgentState,
    *,
    vector_store: VectorStore | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    audit: AuditLogger | None = None,
    llm_call: LLMCallFn | None = None,
    k: int = 5,
) -> dict[str, object]:
    vector_store = vector_store or VectorStore()
    session_factory = session_factory or async_session_factory
    llm_call = llm_call or default_llm_call

    query = last_user_message(state["messages"])
    raw_results = await vector_store.search(query, k=k)

    prompt = load_prompt("discovery").format(
        conversation=format_conversation(state["messages"]),
        search_results=json.dumps(raw_results, default=str),
    )
    raw_response = await llm_call(prompt)

    try:
        parsed = extract_json(raw_response)
        proposed_skus: list[str] = list(parsed.get("skus", []))
        message: str = str(parsed.get("message", "")) or "Here's what I found."
    except ValueError:
        # LLM didn't return valid JSON — fall back to the raw search results, no message text lost.
        proposed_skus = [hit["sku"] for hit in raw_results if hit.get("sku")]
        message = raw_response.strip() or "Here's what I found."

    validated: list[dict[str, object]] = []
    removed: list[str] = []

    async with session_factory() as session:
        for sku in proposed_skus:
            result = await session.execute(select(Product).where(Product.sku == sku))
            product = result.scalar_one_or_none()
            if product is None or product.stock <= 0:
                removed.append(sku)
                continue
            validated.append(
                {
                    "sku": product.sku,
                    "name": product.name,
                    "price_paise": product.price_paise,
                    "category": product.category,
                    "stock": product.stock,
                }
            )

    if audit is not None:
        trace_id = to_uuid(state["trace_id"])
        session_uuid = to_uuid(state["session_id"])

        if removed:
            await audit.log_agent_event(
                actor=Actor.DISCOVERY,
                event_type="agent.discovery.hallucination_caught",
                trace_id=trace_id,
                session_id=session_uuid,
                payload={"removed_skus": removed, "query": query},
                reason="Discovery LLM referenced a SKU that failed catalog validation (not found or out of stock).",
            )

        await audit.log_agent_event(
            actor=Actor.DISCOVERY,
            event_type="agent.discovery.search",
            trace_id=trace_id,
            session_id=session_uuid,
            payload={"query": query, "result_count": len(validated)},
        )

    return {"discovery_results": validated, "final_response": message}
