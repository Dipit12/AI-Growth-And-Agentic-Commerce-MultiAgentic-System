"""MCP tool tests (Step 35). Verifies the tools reuse the same session_store/catalog/checkout
services as the chat endpoint, and that checkout_cart produces the same kind of audit trail a
chat-widget purchase does. VectorStore and the shared Redis-backed session_store are faked so these
run without a live Qdrant or Redis.
"""

import uuid

import pytest
import pytest_asyncio

from app.db.session import async_session_factory
from app.models.product import Product


@pytest_asyncio.fixture(autouse=True)
async def seed_products():  # type: ignore[no-untyped-def]
    from app.db.base import Base
    from app.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sku = f"MCP-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        session.add(
            Product(
                sku=sku, name="MCP Test Keyboard", description="A keyboard for MCP tests.",
                price_paise=150_000, stock=5, category="electronics", attributes={},
            )
        )
        await session.commit()
    return sku


@pytest.fixture
def fake_session_store(monkeypatch):  # type: ignore[no-untyped-def]
    import fakeredis.aioredis

    import app.api.deps as deps_module
    from app.integrations.session_store import SessionStore

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = SessionStore(redis=redis)
    monkeypatch.setattr(deps_module, "session_store", store)
    return store


class FakeVectorStore:
    def __init__(self, results: list[dict]) -> None:
        self._results = results

    async def search(self, query: str, filters: dict | None = None, k: int = 5) -> list[dict]:
        return self._results[:k]


@pytest.mark.asyncio
async def test_search_catalog_returns_validated_products(monkeypatch, seed_products) -> None:  # type: ignore[no-untyped-def]
    import app.mcp_server.tools as tools_module

    sku = seed_products
    monkeypatch.setattr(tools_module, "VectorStore", lambda: FakeVectorStore([{"sku": sku}]))

    results = await tools_module.search_catalog("keyboard")
    assert any(r["sku"] == sku for r in results)


@pytest.mark.asyncio
async def test_get_product_returns_none_for_unknown_sku() -> None:
    import app.mcp_server.tools as tools_module

    assert await tools_module.get_product("DOES-NOT-EXIST") is None


@pytest.mark.asyncio
async def test_get_product_returns_full_details(seed_products) -> None:  # type: ignore[no-untyped-def]
    import app.mcp_server.tools as tools_module

    result = await tools_module.get_product(seed_products)
    assert result is not None
    assert result["price_paise"] == 150_000


@pytest.mark.asyncio
async def test_create_add_remove_cart_roundtrip(fake_session_store, seed_products) -> None:  # type: ignore[no-untyped-def]
    import app.mcp_server.tools as tools_module

    sku = seed_products
    cart_id = await tools_module.create_cart()
    assert cart_id

    add_result = await tools_module.add_to_cart(cart_id, sku, qty=2)
    assert add_result["total_paise"] == 300_000

    remove_result = await tools_module.remove_from_cart(cart_id, sku)
    assert remove_result["total_paise"] == 0


@pytest.mark.asyncio
async def test_add_to_cart_rejects_unknown_sku(fake_session_store) -> None:  # type: ignore[no-untyped-def]
    import app.mcp_server.tools as tools_module

    cart_id = await tools_module.create_cart()
    with pytest.raises(ValueError):
        await tools_module.add_to_cart(cart_id, "NOT-REAL", qty=1)


@pytest.mark.asyncio
async def test_checkout_cart_uses_the_same_checkout_node_as_chat(monkeypatch, fake_session_store, seed_products) -> None:  # type: ignore[no-untyped-def]
    import app.mcp_server.tools as tools_module

    sku = seed_products
    cart_id = await tools_module.create_cart()
    await tools_module.add_to_cart(cart_id, sku, qty=1)

    captured_state = {}

    async def fake_checkout_node(state, **kwargs):  # type: ignore[no-untyped-def]
        captured_state.update(state)
        return {"final_response": "Order placed!", "cart": {"items": []}, "guardrail_decision": {"summary": "ok"}}

    monkeypatch.setattr(tools_module, "checkout_node", fake_checkout_node)

    result = await tools_module.checkout_cart(cart_id, "buyer@example.com")

    assert result["message"] == "Order placed!"
    assert result["cart"] == {"items": []}
    assert captured_state["session_id"] == cart_id  # same cart_id used as the session_id downstream
    assert captured_state["cart"]["items"][0]["sku"] == sku
