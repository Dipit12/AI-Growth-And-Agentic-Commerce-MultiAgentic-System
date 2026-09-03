"""The three failure-mode scenarios from docs/failure_modes.md (Steps 40-42), mirroring
scripts/demo_timeout.py, scripts/demo_denial.py, and scripts/demo_hallucination.py. Unlike
test_agents.py's checkout tests (which mock the whole RazorpayClient), the timeout test here drives
the real RazorpayClient's SIMULATE_RAZORPAY_TIMEOUT seam, so it actually exercises
app/integrations/razorpay_client.py's retry/timeout code path end to end.
"""

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.cart_manager import add_item
from app.agents.checkout import checkout_node
from app.agents.discovery import discovery_node
from app.agents.state import AgentState, empty_cart
from app.audit.logger import AuditLogger
from app.audit.models import AuditEvent
from app.config import Settings
from app.db.base import Base
from app.integrations.razorpay_client import RazorpayClient
from app.integrations.session_store import SessionStore
from app.models.merchant_config import get_or_create_config


@pytest_asyncio.fixture
async def session_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
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


class _RealTimeoutThenFakeLinkClient:
    """Wraps a real RazorpayClient (with SIMULATE_RAZORPAY_TIMEOUT armed) for create_order — so the
    actual retry/timeout code in razorpay_client.py runs — while stubbing create_payment_link so the
    fallback never makes a real network call in a test."""

    def __init__(self, real_client: RazorpayClient) -> None:
        self._real = real_client
        self.payment_link_calls: list[dict[str, Any]] = []

    async def create_order(self, **kwargs: Any) -> dict[str, Any]:
        return await self._real.create_order(**kwargs)

    async def create_payment_link(self, **kwargs: Any) -> dict[str, Any]:
        self.payment_link_calls.append(kwargs)
        return {"id": "plink_test_fallback", "short_url": "https://rzp.io/i/test"}


# ---------------------------------------------------------------------------
# Step 40 — timeout + fallback, no double charge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_timeouts_fall_back_to_payment_link_with_no_double_charge(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    real_client = RazorpayClient(
        settings=Settings(
            RAZORPAY_KEY_ID="rzp_test_failure_modes", RAZORPAY_KEY_SECRET="s",
            SIMULATE_RAZORPAY_TIMEOUT="orders:3",
        )
    )
    client = _RealTimeoutThenFakeLinkClient(real_client)

    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)

    cart = add_item(empty_cart(), "ELEC-002", "Wireless Mouse", 129_900, "electronics", qty=1)
    trace_id = str(uuid.uuid4())
    state: AgentState = {
        "session_id": f"failure-mode-timeout-{uuid.uuid4().hex[:8]}",
        "trace_id": trace_id,
        "messages": [{"role": "user", "content": "checkout"}],
        "cart": cart,
    }

    async def fake_llm(prompt: str) -> str:
        return "We hit a temporary issue placing your order, so here's a payment link instead."

    result = await checkout_node(
        state, audit=audit, session_factory=session_factory, session_store=session_store,
        razorpay_client=client, llm_call=fake_llm,  # type: ignore[arg-type]
    )

    assert result["cart"] == {"items": []}
    assert len(client.payment_link_calls) == 1  # fallback attempted exactly once

    async with session_factory() as session:
        events = (await session.execute(select(AuditEvent).where(AuditEvent.trace_id == uuid.UUID(trace_id)))).scalars().all()

    succeeded_events = [e for e in events if e.event_type.endswith(".succeeded")]
    # Never both a successful order AND a successful payment link for the same checkout.
    assert len(succeeded_events) == 1
    assert succeeded_events[0].event_type == "razorpay.create_payment_link.succeeded"
    assert any(e.event_type == "razorpay.retry_exhausted" for e in events)


# ---------------------------------------------------------------------------
# Step 41 — policy denial, handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_over_cap_checkout_is_denied_with_no_razorpay_call(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    merchant_id = f"failure-mode-denial-{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        config = await get_or_create_config(session, merchant_id)
        config.per_transaction_cap_paise = 50_000  # ₹500
        await session.commit()

    class _MustNotBeCalled:
        async def create_order(self, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("Razorpay must not be called for a denied checkout")

    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)

    cart = add_item(empty_cart(), "ELEC-006", "27-inch 4K Monitor", 500_000, "electronics", qty=1)  # ₹5000
    state: AgentState = {
        "session_id": f"failure-mode-denial-{uuid.uuid4().hex[:8]}",
        "trace_id": str(uuid.uuid4()),
        "messages": [{"role": "user", "content": "checkout"}],
        "cart": cart,
    }

    async def fake_llm(prompt: str) -> str:
        return (
            "This exceeds the per-transaction limit set by the merchant. You can reduce the cart "
            "or contact the merchant to increase your limit."
        )

    result = await checkout_node(
        state, audit=audit, session_factory=session_factory, session_store=session_store,
        razorpay_client=_MustNotBeCalled(), llm_call=fake_llm, merchant_id=merchant_id,  # type: ignore[arg-type]
    )

    assert "cart" not in result  # preserved — never cleared
    assert "exceeds the per-transaction limit" in result["final_response"]
    assert result["guardrail_decision"]["approval_source"] == "denied"


# ---------------------------------------------------------------------------
# Step 42 — hallucination caught by validation
# ---------------------------------------------------------------------------


class _FixedVectorStore:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    async def search(self, query: str, filters: dict | None = None, k: int = 5) -> list[dict[str, Any]]:
        return self._results


@pytest.mark.asyncio
async def test_fake_sku_from_discovery_llm_never_reaches_the_user(session_factory) -> None:  # type: ignore[no-untyped-def]
    from app.models.product import Product

    async with session_factory() as session:
        session.add(
            Product(
                sku="REAL-SKU-1", name="Real Product", description="A real, seeded product.",
                price_paise=99_900, stock=5, category="electronics", attributes={},
            )
        )
        await session.commit()

    async def hallucinating_llm(prompt: str) -> str:
        return '{"skus": ["REAL-SKU-1", "TOTALLY-FAKE-SKU"], "message": "Found two great options!"}'

    events: list[Any] = []

    class RecordingAudit:
        async def log_agent_event(self, **kwargs: Any) -> Any:
            events.append(kwargs)

            class _Evt:
                id = uuid.uuid4()

            return _Evt()

    state: AgentState = {
        "session_id": f"failure-mode-hallucination-{uuid.uuid4().hex[:8]}",
        "trace_id": str(uuid.uuid4()),
        "messages": [{"role": "user", "content": "show me something"}],
        "cart": empty_cart(),
    }

    result = await discovery_node(
        state,
        vector_store=_FixedVectorStore([{"sku": "REAL-SKU-1"}]),
        session_factory=session_factory,
        llm_call=hallucinating_llm,
        audit=RecordingAudit(),  # type: ignore[arg-type]
    )

    shown_skus = [p["sku"] for p in result["discovery_results"]]
    assert "TOTALLY-FAKE-SKU" not in shown_skus
    assert "REAL-SKU-1" in shown_skus
    assert any(e["event_type"] == "agent.discovery.hallucination_caught" for e in events)
