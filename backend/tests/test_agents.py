"""Tests for the LangGraph agent nodes (Steps 24-30). LLM calls and the vector store are injected
as fakes so these run without network access or API keys.
"""

import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.cart_manager import _extract_sku_like_tokens, add_item, cart_manager_node, compute_total, remove_item
from app.agents.checkout import checkout_node
from app.agents.discovery import discovery_node
from app.agents.graph import chat_graph
from app.agents.recommender import recommender_node
from app.agents.router import _parse_intent, router_node
from app.agents.state import AgentState, empty_cart
from app.agents.support import support_node
from app.audit.logger import AuditLogger
from app.db.base import Base
from app.guardrails.idempotency import RetryExhaustedError
from app.integrations.session_store import SessionStore
from app.models.product import Product

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        session.add_all(
            [
                Product(
                    sku="ELEC-001", name="Wireless Mechanical Keyboard",
                    description="A hot-swappable mechanical keyboard.", price_paise=349900,
                    stock=10, category="electronics", attributes={},
                ),
                Product(
                    sku="ELEC-002", name="Wireless Mouse", description="An ergonomic mouse.",
                    price_paise=129900, stock=20, category="electronics", attributes={},
                ),
                Product(
                    sku="OUT-OF-STOCK", name="Ghost Product", description="Should never surface.",
                    price_paise=1000, stock=0, category="electronics", attributes={},
                ),
            ]
        )
        await session.commit()

    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def fake_redis():  # type: ignore[no-untyped-def]
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield redis
    finally:
        await redis.aclose()


def _state(intent: str | None = None, messages: list[dict] | None = None, cart: dict | None = None) -> AgentState:
    state: AgentState = {
        "session_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "messages": messages or [{"role": "user", "content": "hello"}],
        "cart": cart or empty_cart(),
    }
    if intent is not None:
        state["current_intent"] = intent  # type: ignore[typeddict-item]
    return state


class FakeVectorStore:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    async def search(self, query: str, filters: dict | None = None, k: int = 5) -> list[dict[str, Any]]:
        return self._results[:k]


# ---------------------------------------------------------------------------
# Router (Step 24)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("discover", "discover"),
        ("  Checkout.  ", "checkout"),
        ("CART", "cart"),
        ("recommend", "recommend"),
        ("support", "support"),
        ("something unrelated and garbled", "support"),
    ],
)
def test_parse_intent_handles_case_and_noise(raw: str, expected: str) -> None:
    assert _parse_intent(raw) == expected


@pytest.mark.asyncio
async def test_router_node_classifies_obvious_checkout_message() -> None:
    async def fake_llm(prompt: str) -> str:
        return "checkout"

    state = _state(messages=[{"role": "user", "content": "ok let's pay, checkout now"}])
    result = await router_node(state, llm_call=fake_llm)
    assert result["current_intent"] == "checkout"


@pytest.mark.asyncio
async def test_router_node_defaults_to_support_on_unparseable_output() -> None:
    async def fake_llm(prompt: str) -> str:
        return "I have no idea what you mean!!"

    state = _state()
    result = await router_node(state, llm_call=fake_llm)
    assert result["current_intent"] == "support"


# ---------------------------------------------------------------------------
# Discovery (Step 25, plus the Step 42 hallucination-catch seam)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_finds_a_keyboard(session_factory) -> None:  # type: ignore[no-untyped-def]
    vector_store = FakeVectorStore([{"sku": "ELEC-001", "score": 0.9}])

    async def fake_llm(prompt: str) -> str:
        return '{"skus": ["ELEC-001"], "message": "Found a great wireless keyboard for you."}'

    state = _state(messages=[{"role": "user", "content": "I need a wireless keyboard"}])
    result = await discovery_node(state, vector_store=vector_store, session_factory=session_factory, llm_call=fake_llm)

    skus = [p["sku"] for p in result["discovery_results"]]
    assert "ELEC-001" in skus
    assert "keyboard" in result["final_response"].lower()


@pytest.mark.asyncio
async def test_discovery_catches_hallucinated_sku(session_factory) -> None:  # type: ignore[no-untyped-def]
    vector_store = FakeVectorStore([{"sku": "ELEC-001", "score": 0.9}])

    async def fake_llm_hallucinating(prompt: str) -> str:
        # The LLM references a SKU that was never returned by the vector search and does not exist.
        return '{"skus": ["ELEC-001", "FAKE-999"], "message": "Found two great options."}'

    events: list[dict] = []

    class RecordingAudit:
        async def log_agent_event(self, **kwargs):  # type: ignore[no-untyped-def]
            events.append(kwargs)

            class _Evt:
                id = uuid.uuid4()

            return _Evt()

    state = _state(messages=[{"role": "user", "content": "show me keyboards"}])
    result = await discovery_node(
        state, vector_store=vector_store, session_factory=session_factory,
        llm_call=fake_llm_hallucinating, audit=RecordingAudit(),  # type: ignore[arg-type]
    )

    skus = [p["sku"] for p in result["discovery_results"]]
    assert "FAKE-999" not in skus
    assert "ELEC-001" in skus
    assert any(e["event_type"] == "agent.discovery.hallucination_caught" for e in events)


@pytest.mark.asyncio
async def test_discovery_excludes_out_of_stock_products(session_factory) -> None:  # type: ignore[no-untyped-def]
    vector_store = FakeVectorStore([{"sku": "OUT-OF-STOCK", "score": 0.5}])

    async def fake_llm(prompt: str) -> str:
        return '{"skus": ["OUT-OF-STOCK"], "message": "Found something."}'

    state = _state()
    result = await discovery_node(state, vector_store=vector_store, session_factory=session_factory, llm_call=fake_llm)
    assert result["discovery_results"] == []


# ---------------------------------------------------------------------------
# Cart manager (Step 26) — deterministic math
# ---------------------------------------------------------------------------


def test_add_item_is_pure_and_exact() -> None:
    cart = empty_cart()
    cart = add_item(cart, "ELEC-001", "Keyboard", 349900, "electronics", qty=2)
    assert compute_total(cart) == 699800


def test_add_item_increments_existing_line_rather_than_duplicating() -> None:
    cart = empty_cart()
    cart = add_item(cart, "ELEC-001", "Keyboard", 349900, "electronics", qty=1)
    cart = add_item(cart, "ELEC-001", "Keyboard", 349900, "electronics", qty=1)
    assert len(cart["items"]) == 1
    assert cart["items"][0]["qty"] == 2


def test_remove_item_is_pure_and_no_op_if_absent() -> None:
    cart = empty_cart()
    cart = add_item(cart, "ELEC-001", "Keyboard", 349900, "electronics")
    cart = remove_item(cart, "does-not-exist")
    assert len(cart["items"]) == 1
    cart = remove_item(cart, "ELEC-001")
    assert cart["items"] == []


def test_compute_total_has_no_floating_point_error() -> None:
    cart = empty_cart()
    for _ in range(3):
        cart = add_item(cart, "ELEC-002", "Mouse", 129900, "electronics", qty=1)
    assert compute_total(cart) == 389700
    assert isinstance(compute_total(cart), int)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("add ELEC-002 to my cart", ["ELEC-002"]),
        ("add elec-002 please", ["ELEC-002"]),
        ("I want the FIT-010 and HOM-005", ["FIT-010", "HOM-005"]),
        ("what's in my cart?", []),
    ],
)
def test_extract_sku_like_tokens(text: str, expected: list[str]) -> None:
    assert _extract_sku_like_tokens(text) == expected


@pytest.mark.asyncio
async def test_cart_manager_node_offers_a_directly_mentioned_sku_as_a_candidate(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    """A SKU mentioned directly (no prior discovery step) must still be resolvable — regression for
    a real failure seen with a small local model that returned sku=null when the prompt's candidate
    list was empty, even though the user had typed the SKU verbatim."""
    session_store = SessionStore(redis=fake_redis)
    seen_candidates = {}

    async def fake_llm(prompt: str) -> str:
        seen_candidates["prompt"] = prompt
        return '{"operation": "add", "sku": "ELEC-002", "qty": 1}'

    state = _state(messages=[{"role": "user", "content": "add ELEC-002 to my cart, quantity 1"}])
    result = await cart_manager_node(
        state, session_factory=session_factory, session_store=session_store, llm_call=fake_llm,
    )

    assert "ELEC-002" in seen_candidates["prompt"]
    assert compute_total(result["cart"]) == 129900


@pytest.mark.asyncio
async def test_cart_manager_node_add_survives_session_roundtrip(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    session_store = SessionStore(redis=fake_redis)

    async def fake_llm(prompt: str) -> str:
        return '{"operation": "add", "sku": "ELEC-001", "qty": 1}'

    state = _state(messages=[{"role": "user", "content": "add the keyboard"}])
    result = await cart_manager_node(
        state, session_factory=session_factory, session_store=session_store, llm_call=fake_llm,
    )

    assert compute_total(result["cart"]) == 349900

    persisted = await session_store.get_cart(state["session_id"])
    assert compute_total(persisted) == 349900


# ---------------------------------------------------------------------------
# Recommender (Step 28)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommender_surfaces_a_mouse_for_a_keyboard_cart() -> None:
    vector_store = FakeVectorStore(
        [
            {"sku": "ELEC-002", "name": "Wireless Mouse", "category": "electronics", "score": 0.8},
            {"sku": "HOM-005", "name": "Wall Clock", "category": "home", "score": 0.7},
        ]
    )

    async def fake_llm(prompt: str) -> str:
        return "A matching wireless mouse for your new keyboard.\nA nice desk accessory too."

    cart = add_item(empty_cart(), "ELEC-001", "Keyboard", 349900, "electronics")
    state = _state(cart=cart)

    result = await recommender_node(state, vector_store=vector_store, llm_call=fake_llm)

    skus = [r["sku"] for r in result["recommendations"]]
    assert "ELEC-002" in skus
    assert skus[0] == "ELEC-002"  # same-category candidate ranked first, deterministically


@pytest.mark.asyncio
async def test_recommender_returns_nothing_for_an_empty_cart() -> None:
    result = await recommender_node(_state(cart=empty_cart()))
    assert result["recommendations"] == []


# ---------------------------------------------------------------------------
# Support (Step 29)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_support_node_handles_off_topic_message_gracefully() -> None:
    async def fake_llm(prompt: str) -> str:
        return "I'm here to help you shop! I can't check the weather, but let me know what you're looking for."

    state = _state(messages=[{"role": "user", "content": "what's the weather like today?"}])
    result = await support_node(state, llm_call=fake_llm)
    assert "final_response" in result
    assert result["final_response"]


# ---------------------------------------------------------------------------
# Graph wiring (Step 30)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,expected_node",
    [
        ("discover", "discovery"),
        ("recommend", "recommender"),
        ("cart", "cart_manager"),
        ("checkout", "checkout"),
        ("support", "support"),
        ("anything_unrecognized", "support"),
    ],
)
def test_route_from_router_maps_every_intent(intent: str, expected_node: str) -> None:
    from app.agents.graph import _route_from_router

    assert _route_from_router(_state(intent=intent)) == expected_node


def test_every_intent_maps_to_a_reachable_node() -> None:
    from app.agents.graph import _INTENT_TO_NODE

    graph_nodes = set(chat_graph.get_graph().nodes.keys())
    for intent, node_name in _INTENT_TO_NODE.items():
        assert node_name in graph_nodes, f"intent '{intent}' routes to unreachable node '{node_name}'"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent,expected_marker",
    [
        ("discover", "discovery-reached"),
        ("recommend", "recommender-reached"),
        ("cart", "cart_manager-reached"),
        ("checkout", "checkout-reached"),
        ("support", "support-reached"),
    ],
)
async def test_graph_actually_routes_every_intent_without_langgraph_error(monkeypatch, intent: str, expected_marker: str) -> None:  # type: ignore[no-untyped-def]
    """End-to-end regression test: add_conditional_edges' path_map was once set to _INTENT_TO_NODE
    even though _route_from_router already returns node names, not intents. LangGraph then tried to
    look up the *node name* as a key in that dict, which only happens to exist for "checkout" and
    "support" (where node name == intent name) — "cart" -> "cart_manager" and friends raised
    KeyError at runtime. Neither test_route_from_router_maps_every_intent (tests the pure function
    in isolation) nor test_every_intent_maps_to_a_reachable_node (checks node existence, not
    reachability via the actual conditional edge) would catch that — this drives a real
    `ainvoke()` through the compiled graph for every intent instead.
    """
    import app.agents.graph as graph_module

    async def fake_router(state: dict, **kwargs: object) -> dict:
        return {"current_intent": intent}

    def make_stub(marker: str):  # type: ignore[no-untyped-def]
        async def stub(state: dict, **kwargs: object) -> dict:
            return {"final_response": marker}

        return stub

    monkeypatch.setattr(graph_module, "router_node", fake_router)
    monkeypatch.setattr(graph_module, "discovery_node", make_stub("discovery-reached"))
    monkeypatch.setattr(graph_module, "recommender_node", make_stub("recommender-reached"))
    monkeypatch.setattr(graph_module, "cart_manager_node", make_stub("cart_manager-reached"))
    monkeypatch.setattr(graph_module, "checkout_node", make_stub("checkout-reached"))
    monkeypatch.setattr(graph_module, "support_node", make_stub("support-reached"))

    fresh_graph = graph_module.build_graph()
    result = await fresh_graph.ainvoke(_state())
    assert result["final_response"] == expected_marker


# ---------------------------------------------------------------------------
# Checkout (Step 27) — the only money node
# ---------------------------------------------------------------------------


def _cart_with_keyboard() -> dict:
    return add_item(empty_cart(), "ELEC-001", "Keyboard", 100_000, "electronics", qty=1)


@pytest.mark.asyncio
async def test_checkout_happy_path_completes_a_purchase(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)

    class FakeRazorpay:
        async def create_order(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"id": "order_happy", "status": "created"}

    async def fake_llm(prompt: str) -> str:
        return "Your order is confirmed!"

    state = _state(cart=_cart_with_keyboard())
    result = await checkout_node(
        state, audit=audit, session_factory=session_factory, session_store=session_store,
        razorpay_client=FakeRazorpay(), llm_call=fake_llm,  # type: ignore[arg-type]
    )

    assert result["cart"] == {"items": []}
    assert "confirmed" in result["final_response"].lower()


@pytest.mark.asyncio
async def test_checkout_falls_back_to_payment_link_after_repeated_timeouts(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)

    class FlakyThenFallbackRazorpay:
        async def create_order(self, **kwargs):  # type: ignore[no-untyped-def]
            raise httpx.TimeoutException("simulated timeout")

        async def create_payment_link(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"id": "plink_fallback", "short_url": "https://rzp.io/i/fake"}

    async def fake_llm(prompt: str) -> str:
        return "We hit a temporary issue, so here's a payment link instead."

    state = _state(cart=_cart_with_keyboard())
    result = await checkout_node(
        state, audit=audit, session_factory=session_factory, session_store=session_store,
        razorpay_client=FlakyThenFallbackRazorpay(), llm_call=fake_llm,  # type: ignore[arg-type]
    )

    assert result["cart"] == {"items": []}
    assert "payment link" in result["final_response"].lower()


@pytest.mark.asyncio
async def test_checkout_denies_gracefully_over_transaction_cap(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)

    class ShouldNeverBeCalledRazorpay:
        async def create_order(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Razorpay must not be called for a denied checkout")

    async def fake_llm(prompt: str) -> str:
        return "This exceeds the per-transaction limit — your cart is safe, feel free to adjust it."

    huge_cart = add_item(empty_cart(), "ELEC-001", "Keyboard", 999_999_999, "electronics", qty=1)
    state = _state(cart=huge_cart)

    result = await checkout_node(
        state, audit=audit, session_factory=session_factory, session_store=session_store,
        razorpay_client=ShouldNeverBeCalledRazorpay(), llm_call=fake_llm,  # type: ignore[arg-type]
    )

    # A denied checkout returns no "cart" key at all — LangGraph merges partial updates, so the
    # cart in the overall session state is left untouched (never cleared) rather than overwritten.
    assert "cart" not in result
    assert "guardrail_decision" in result
