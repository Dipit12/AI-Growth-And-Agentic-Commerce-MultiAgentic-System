"""Discovery agent proposes a non-existent SKU; the validation layer catches and strips it before
it ever reaches the user, logging an `agent.discovery.hallucination_caught` audit event (Step 42).

Run with: python -m scripts.demo_hallucination
Then replay the printed trace_id with: python -m scripts.replay_audit <trace_id> --verbose
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy import select

from app.agents.discovery import discovery_node
from app.agents.state import AgentState, empty_cart
from app.audit.logger import AuditLogger
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.logging_config import configure_logging, get_logger
from app.models.product import Product

logger = get_logger("demo_hallucination")

REAL_SKU = "ELEC-001"
FAKE_SKU = "FAKE-SKU-999"


class _FixedVectorStore:
    """Stands in for Qdrant: always returns the one real product, regardless of query."""

    async def search(self, query: str, filters: dict[str, Any] | None = None, k: int = 5) -> list[dict[str, Any]]:
        return [{"sku": REAL_SKU, "score": 0.91}]


async def _hallucinating_llm(prompt: str) -> str:
    """Stands in for the discovery LLM: invents a SKU that was never returned by the catalog search."""
    return (
        f'{{"skus": ["{REAL_SKU}", "{FAKE_SKU}"], '
        f'"message": "Found a great keyboard, plus a bonus deal you will love!"}}'
    )


async def _ensure_real_product_seeded() -> None:
    async with async_session_factory() as session:
        existing = await session.execute(select(Product).where(Product.sku == REAL_SKU))
        if existing.scalar_one_or_none() is None:
            session.add(
                Product(
                    sku=REAL_SKU, name="Wireless Mechanical Keyboard",
                    description="A hot-swappable mechanical keyboard.", price_paise=349_900,
                    stock=10, category="electronics", attributes={},
                )
            )
            await session.commit()


async def main() -> None:
    configure_logging()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_real_product_seeded()

    audit = AuditLogger(async_session_factory)
    audit.start()

    trace_id = str(uuid.uuid4())
    state: AgentState = {
        "session_id": f"demo-hallucination-{uuid.uuid4().hex[:8]}",
        "trace_id": trace_id,
        "messages": [{"role": "user", "content": "show me keyboards"}],
        "cart": empty_cart(),
    }

    result = await discovery_node(
        state, vector_store=_FixedVectorStore(), llm_call=_hallucinating_llm, audit=audit,
    )

    shown_skus = [p["sku"] for p in result["discovery_results"]]
    assert FAKE_SKU not in shown_skus, "hallucinated SKU leaked to the user!"

    print(f"trace_id: {trace_id}")
    print(f"LLM proposed: [{REAL_SKU}, {FAKE_SKU}]")
    print(f"Shown to user after validation: {shown_skus}")
    print(f"\nReplay with: python -m scripts.replay_audit {trace_id} --verbose")

    await audit.stop()


if __name__ == "__main__":
    asyncio.run(main())
