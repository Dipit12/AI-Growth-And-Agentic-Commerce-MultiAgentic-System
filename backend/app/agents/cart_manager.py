"""Cart manager node — deterministic add/remove/total operations. Layer 2 (agents). The LLM only
translates the user's message into a structured operation (invariant #7: it never does the
arithmetic). Cannot call Razorpay.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.llm import LLMCallFn, default_llm_call, format_conversation
from app.agents.prompts.loader import load_prompt
from app.agents.state import Cart, CartItem, AgentState
from app.audit.logger import AuditLogger
from app.audit.models import Actor
from app.db.session import async_session_factory
from app.integrations.session_store import SessionStore
from app.models.product import Product
from app.utils.ids import to_uuid
from app.utils.llm_json import extract_json


def add_item(
    cart: Cart, sku: str, name: str, unit_price_paise: int, category: str, qty: int = 1
) -> Cart:
    """Pure. Increments quantity if the SKU is already present, otherwise appends a new line."""
    items: list[CartItem] = []
    found = False
    for item in cart["items"]:
        if item["sku"] == sku:
            items.append({**item, "qty": item["qty"] + qty})
            found = True
        else:
            items.append(item)
    if not found:
        items.append(
            {"sku": sku, "name": name, "qty": qty, "unit_price_paise": unit_price_paise, "category": category}
        )
    return {"items": items}


def remove_item(cart: Cart, sku: str) -> Cart:
    """Pure. No-op if the SKU isn't in the cart."""
    return {"items": [item for item in cart["items"] if item["sku"] != sku]}


def compute_total(cart: Cart) -> int:
    """Pure integer paise arithmetic — never floating point, never LLM-touched."""
    return sum(item["qty"] * item["unit_price_paise"] for item in cart["items"])


async def cart_manager_node(
    state: AgentState,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    session_store: SessionStore | None = None,
    audit: AuditLogger | None = None,
    llm_call: LLMCallFn | None = None,
) -> dict[str, object]:
    session_factory = session_factory or async_session_factory
    session_store = session_store or SessionStore()
    llm_call = llm_call or default_llm_call

    cart = state["cart"]
    candidate_skus = [r["sku"] for r in state.get("discovery_results", []) if "sku" in r]
    candidate_skus += [item["sku"] for item in cart["items"]]

    prompt = load_prompt("cart_manager").format(
        candidate_skus=", ".join(candidate_skus) or "(none)",
        cart_contents=", ".join(f"{i['sku']} x{i['qty']}" for i in cart["items"]) or "(empty)",
        conversation=format_conversation(state["messages"]),
    )
    raw_response = await llm_call(prompt)

    try:
        parsed = extract_json(raw_response)
        operation = str(parsed.get("operation", "none"))
        sku = parsed.get("sku")
        qty = int(parsed.get("qty") or 1)
    except ValueError:
        operation, sku, qty = "none", None, 1

    new_cart = cart
    message = "I didn't catch a specific change to make to your cart — could you rephrase that?"

    if operation == "add" and sku:
        async with session_factory() as session:
            result = await session.execute(select(Product).where(Product.sku == sku))
            product = result.scalar_one_or_none()
        if product is not None and product.stock > 0:
            new_cart = add_item(
                cart, product.sku, product.name, product.price_paise, product.category, qty=qty
            )
            message = f"Added {qty} x {product.name} to your cart."
        else:
            message = f"Sorry, I couldn't find '{sku}' in stock to add to your cart."

    elif operation == "remove" and sku:
        new_cart = remove_item(cart, sku)
        message = f"Removed {sku} from your cart."

    if session_store is not None:
        await session_store.set_cart(state["session_id"], dict(new_cart))

    if audit is not None:
        await audit.log_agent_event(
            actor=Actor.CART_MANAGER,
            event_type="agent.cart_manager.operation",
            trace_id=to_uuid(state["trace_id"]),
            session_id=to_uuid(state["session_id"]),
            payload={"operation": operation, "sku": sku, "qty": qty, "total_paise": compute_total(new_cart)},
        )

    return {"cart": new_cart, "final_response": message}
