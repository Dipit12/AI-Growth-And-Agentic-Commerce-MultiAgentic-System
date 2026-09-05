"""Recommender node — upsell/cross-sell suggestions after a cart change. Layer 2 (agents). Ranking
is a deterministic category-affinity heuristic; the LLM only writes the one-sentence framing per
candidate (invariant #7: LLMs never do the ranking/arithmetic). Cannot call Razorpay or edit the cart.
"""

import json

from app.agents.llm import LLMCallFn, default_llm_call
from app.agents.prompts.loader import load_prompt
from app.agents.state import AgentState
from app.audit.logger import AuditLogger
from app.audit.models import Actor
from app.audit.singleton import get_audit_logger
from app.integrations.vector_store import VectorStore
from app.utils.ids import to_uuid

TOP_N = 3


def _rank_by_affinity(candidates: list[dict], cart_categories: set[str]) -> list[dict]:
    """Pure. Same-category candidates first (preserving vector-search relevance order within each
    group), then everything else."""

    def sort_key(candidate: dict, index: int) -> tuple[int, int]:
        same_category = candidate.get("category") in cart_categories
        return (0 if same_category else 1, index)

    indexed = list(enumerate(candidates))
    indexed.sort(key=lambda pair: sort_key(pair[1], pair[0]))
    return [candidate for _, candidate in indexed]


async def recommender_node(
    state: AgentState,
    *,
    vector_store: VectorStore | None = None,
    audit: AuditLogger | None = None,
    llm_call: LLMCallFn | None = None,
    top_n: int = TOP_N,
) -> dict[str, object]:
    vector_store = vector_store or VectorStore()
    audit = audit or get_audit_logger()
    llm_call = llm_call or default_llm_call

    cart = state["cart"]
    if not cart["items"]:
        return {"recommendations": []}

    query = " ".join(item["name"] for item in cart["items"])
    in_cart_skus = {item["sku"] for item in cart["items"]}
    cart_categories = {item["category"] for item in cart["items"]}

    raw_results = await vector_store.search(query, k=top_n * 3)
    candidates = [r for r in raw_results if r.get("sku") not in in_cart_skus]
    ranked = _rank_by_affinity(candidates, cart_categories)[:top_n]

    if not ranked:
        return {"recommendations": []}

    prompt = load_prompt("recommender").format(
        cart_contents=", ".join(item["name"] for item in cart["items"]),
        candidates=json.dumps(ranked, default=str),
    )
    raw_response = await llm_call(prompt)
    reasons = [line.strip() for line in raw_response.strip().splitlines() if line.strip()]

    recommendations = []
    for i, candidate in enumerate(ranked):
        reason = reasons[i] if i < len(reasons) else "A popular pairing with items already in your cart."
        recommendations.append({**candidate, "reason": reason})

    if audit is not None:
        await audit.log_agent_event(
            actor=Actor.RECOMMENDER,
            event_type="agent.recommender.suggested",
            trace_id=to_uuid(state["trace_id"]),
            session_id=to_uuid(state["session_id"]),
            payload={"skus": [c.get("sku") for c in recommendations]},
        )

    return {"recommendations": recommendations}
