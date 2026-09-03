"""Shared LangGraph state schema. Layer 2 (agents) — a TypedDict so LangGraph's StateGraph can merge
partial node updates the standard way. Payloads that cross into guardrails/audit still use the
Pydantic models defined there (ProposedAction, PolicyDecision, ReasonTrace, ...).
"""

from typing import Any, Literal, NotRequired, TypedDict

Intent = Literal["discover", "recommend", "cart", "checkout", "support"]


class CartItem(TypedDict):
    sku: str
    name: str
    qty: int
    unit_price_paise: int
    category: str


class Cart(TypedDict):
    items: list[CartItem]


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class AgentState(TypedDict):
    session_id: str
    trace_id: str
    messages: list[ChatMessage]
    cart: Cart
    current_intent: NotRequired[Intent]
    discovery_results: NotRequired[list[dict[str, Any]]]
    recommendations: NotRequired[list[dict[str, Any]]]
    pending_action: NotRequired[dict[str, Any] | None]
    guardrail_decision: NotRequired[dict[str, Any] | None]
    final_response: NotRequired[str]
    pending_approval_id: NotRequired[str | None]


def empty_cart() -> Cart:
    return {"items": []}


def new_state(session_id: str, trace_id: str) -> AgentState:
    return AgentState(
        session_id=session_id,
        trace_id=trace_id,
        messages=[],
        cart=empty_cart(),
    )
