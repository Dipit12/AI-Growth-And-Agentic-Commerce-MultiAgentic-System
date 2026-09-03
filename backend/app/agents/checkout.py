"""Checkout node — converts a confirmed cart into a paid order. Layer 2 (agents). THE ONLY MONEY NODE:
the only file in app/agents/ that imports app/integrations/razorpay_client.py, and every write goes
through guardrails.gate_money_action rather than calling the Razorpay client directly (CLAUDE.md
invariants #1-#2). On a native-order failure after retries, it falls back to a gated payment link.
"""

import hashlib
import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.cart_manager import compute_total
from app.agents.llm import LLMCallFn, default_llm_call
from app.agents.prompts.loader import load_prompt
from app.agents.state import AgentState, Cart
from app.audit.logger import AuditLogger
from app.db.session import async_session_factory
from app.guardrails.gate import GateResult, gate_money_action
from app.guardrails.idempotency import RetryExhaustedError
from app.integrations.razorpay_client import RazorpayClient
from app.integrations.session_store import SessionStore
from app.models.merchant_config import DEFAULT_MERCHANT_ID
from app.models.policy import ProposedAction
from app.utils.ids import to_uuid

DEFAULT_PAYMENT_METHOD = "card"


def _cart_hash(cart: Cart) -> str:
    canonical = json.dumps(sorted(cart["items"], key=lambda item: item["sku"]), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _cart_category(cart: Cart) -> str:
    """Policy categories are per-action, but a cart can mix categories. A uniform cart is checked
    against its real category; a mixed cart falls back to a label merchants can still block."""
    categories = {item["category"] for item in cart["items"]}
    return next(iter(categories)) if len(categories) == 1 else "mixed_cart"


async def _explain(llm_call: LLMCallFn, outcome: dict[str, object]) -> str:
    prompt = load_prompt("checkout").format(outcome=json.dumps(outcome, default=str))
    return await llm_call(prompt)


async def checkout_node(
    state: AgentState,
    *,
    audit: AuditLogger | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    session_store: SessionStore | None = None,
    razorpay_client: RazorpayClient | None = None,
    llm_call: LLMCallFn | None = None,
    merchant_id: str = DEFAULT_MERCHANT_ID,
) -> dict[str, object]:
    session_factory = session_factory or async_session_factory
    session_store = session_store or SessionStore()
    audit = audit or AuditLogger(session_factory)
    razorpay_client = razorpay_client or RazorpayClient()
    llm_call = llm_call or default_llm_call

    cart = state["cart"]
    total_paise = compute_total(cart)
    trace_id = to_uuid(state["trace_id"])
    session_id = to_uuid(state["session_id"])

    if total_paise <= 0:
        return {"final_response": "Your cart is empty — add something before checking out."}

    cart_hash = _cart_hash(cart)
    category = _cart_category(cart)

    order_action = ProposedAction(
        action_type="create_order",
        amount_paise=total_paise,
        category=category,
        payment_method=DEFAULT_PAYMENT_METHOD,
        session_id=state["session_id"],
        cart_hash=cart_hash,
    )

    async def create_order_call(idempotency_key: str) -> dict[str, object]:
        return await razorpay_client.create_order(
            amount_paise=total_paise,
            currency="INR",
            receipt=f"cart-{cart_hash[:16]}",
            notes={"session_id": state["session_id"]},
            idempotency_key=idempotency_key,
        )

    try:
        order_result = await gate_money_action(
            order_action, trace_id, session_id, create_order_call,
            audit=audit, session_factory=session_factory, session_store=session_store,
            merchant_id=merchant_id,
        )
    except RetryExhaustedError as exc:
        return await _fallback_to_payment_link(
            order_action, total_paise, cart_hash, trace_id, session_id, exc,
            audit=audit, session_factory=session_factory, session_store=session_store,
            razorpay_client=razorpay_client, llm_call=llm_call, merchant_id=merchant_id,
        )

    if not order_result.success:
        message = await _explain(llm_call, _summarize(order_result, kind="denied", amount_paise=total_paise))
        return {"final_response": message, "guardrail_decision": order_result.reason_trace.model_dump()}

    await session_store.set_cart(state["session_id"], {"items": []})
    message = await _explain(llm_call, _summarize(order_result, kind="order_success", amount_paise=total_paise))

    return {
        "final_response": message,
        "cart": {"items": []},
        "guardrail_decision": order_result.reason_trace.model_dump(),
    }


async def _fallback_to_payment_link(
    order_action: ProposedAction,
    total_paise: int,
    cart_hash: str,
    trace_id: object,
    session_id: object,
    original_error: RetryExhaustedError,
    *,
    audit: AuditLogger,
    session_factory: async_sessionmaker[AsyncSession],
    session_store: SessionStore,
    razorpay_client: RazorpayClient,
    llm_call: LLMCallFn,
    merchant_id: str,
) -> dict[str, object]:
    link_action = order_action.model_copy(update={"action_type": "create_payment_link"})

    async def create_link_call(idempotency_key: str) -> dict[str, object]:
        return await razorpay_client.create_payment_link(
            amount_paise=total_paise,
            description=f"Order for cart {cart_hash[:16]}",
            customer={},
            idempotency_key=idempotency_key,
        )

    link_result = await gate_money_action(
        link_action, trace_id, session_id, create_link_call,  # type: ignore[arg-type]
        audit=audit, session_factory=session_factory, session_store=session_store,
        merchant_id=merchant_id,
    )

    if not link_result.success:
        message = await _explain(
            llm_call, _summarize(link_result, kind="denied", amount_paise=total_paise)
        )
        return {"final_response": message, "guardrail_decision": link_result.reason_trace.model_dump()}

    await session_store.set_cart(str(session_id), {"items": []})
    outcome = _summarize(link_result, kind="payment_link_fallback", amount_paise=total_paise)
    outcome["original_error"] = str(original_error)
    message = await _explain(llm_call, outcome)

    return {
        "final_response": message,
        "cart": {"items": []},
        "guardrail_decision": link_result.reason_trace.model_dump(),
    }


def _summarize(result: GateResult, *, kind: str, amount_paise: int) -> dict[str, object]:
    return {
        "kind": kind,
        "success": result.success,
        "verdict": result.verdict,
        "amount_paise": amount_paise,
        "response": result.response,
        "reason": result.reason_trace.summary,
    }
