"""MCP tool implementations. Layer 1 (surfaces). Every tool calls into the SAME services
app/api/chat.py uses — VectorStore, Postgres, session_store, and (for checkout) the exact
app/agents/checkout.py::checkout_node the chat widget invokes — never a parallel implementation.
Mutating tools therefore inherit the same guardrails/audit trail as a chat-widget purchase
(CLAUDE.md invariants #1-#2: checkout_node remains the only place that reaches Razorpay).

Plain, MCP-agnostic async functions so they're independently testable; app/mcp_server/server.py
registers each one onto the MCPServer instance.
"""

import uuid
from typing import Any

from app.agents.cart_manager import add_item, compute_total, remove_item
from app.agents.checkout import checkout_node
from app.agents.state import AgentState, Cart, empty_cart
from app.api.deps import get_audit_logger, get_session_store
from app.db.session import async_session_factory
from app.integrations.vector_store import VectorStore
from app.models.product import Product, get_product_by_sku


def _product_dict(product: Product) -> dict[str, Any]:
    return {
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "price_paise": product.price_paise,
        "category": product.category,
        "stock": product.stock,
    }


async def search_catalog(query: str, category: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Semantically search the product catalog. Returns products validated against the live
    catalog (never a stale or hallucinated SKU) — each with sku, name, description, price_paise,
    category, and stock. `category` optionally filters results; `limit` caps the count (default 5).
    """
    store = VectorStore()
    filters = {"category": category} if category else None
    raw_results = await store.search(query, filters=filters, k=limit)

    validated: list[dict[str, Any]] = []
    async with async_session_factory() as session:
        for hit in raw_results:
            sku = hit.get("sku")
            product = await get_product_by_sku(session, sku) if sku else None
            if product is not None and product.stock > 0:
                validated.append(_product_dict(product))
    return validated


async def get_product(sku: str) -> dict[str, Any] | None:
    """Look up a single product by SKU (case-insensitive). Returns null if the SKU doesn't exist."""
    async with async_session_factory() as session:
        product = await get_product_by_sku(session, sku)
    return _product_dict(product) if product is not None else None


async def create_cart() -> str:
    """Create a new, empty cart and return its cart_id. Call this once before add_to_cart."""
    cart_id = str(uuid.uuid4())
    await get_session_store().set_cart(cart_id, dict(empty_cart()))
    return cart_id


def _cart_from_raw(raw: dict[str, Any]) -> Cart:
    return {"items": raw.get("items", [])}  # type: ignore[typeddict-item]


async def add_to_cart(cart_id: str, sku: str, qty: int = 1) -> dict[str, Any]:
    """Add `qty` units of a product (by SKU) to the cart. Fails if the SKU doesn't exist or is out
    of stock. Returns the updated cart and its total in paise."""
    session_store = get_session_store()
    cart = _cart_from_raw(await session_store.get_cart(cart_id))

    async with async_session_factory() as session:
        product = await get_product_by_sku(session, sku)

    if product is None or product.stock <= 0:
        raise ValueError(f"SKU '{sku}' does not exist or is out of stock.")

    new_cart = add_item(cart, product.sku, product.name, product.price_paise, product.category, qty=qty)
    await session_store.set_cart(cart_id, dict(new_cart))
    return {"cart": new_cart, "total_paise": compute_total(new_cart)}


async def remove_from_cart(cart_id: str, sku: str) -> dict[str, Any]:
    """Remove a product (by SKU) from the cart. A no-op if the SKU isn't in the cart. Returns the
    updated cart and its total in paise."""
    session_store = get_session_store()
    cart = _cart_from_raw(await session_store.get_cart(cart_id))

    new_cart = remove_item(cart, sku)
    await session_store.set_cart(cart_id, dict(new_cart))
    return {"cart": new_cart, "total_paise": compute_total(new_cart)}


async def checkout_cart(cart_id: str, customer_email: str) -> dict[str, Any]:
    """Check out the cart via a test-mode Razorpay payment, gated by merchant policy exactly like a
    chat-widget purchase. May require merchant approval for large amounts — this call blocks until
    that's resolved or times out, so treat a slow response as normal for a gated checkout. Returns a
    message, the (now-cleared-on-success) cart, and the guardrail decision that was applied.
    """
    session_store = get_session_store()
    audit = get_audit_logger()
    audit.start()

    cart = _cart_from_raw(await session_store.get_cart(cart_id))
    trace_id = str(uuid.uuid4())

    state: AgentState = {
        "session_id": cart_id,
        "trace_id": trace_id,
        "messages": [{"role": "user", "content": f"checkout for {customer_email}"}],
        "cart": cart,
    }

    result = await checkout_node(
        state, audit=audit, session_factory=async_session_factory, session_store=session_store
    )

    return {
        "message": result.get("final_response", ""),
        "cart": result.get("cart", cart),
        "guardrail_decision": result.get("guardrail_decision"),
        "trace_id": trace_id,
    }
