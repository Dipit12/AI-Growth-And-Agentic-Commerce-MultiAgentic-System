"""Cap-exceeded checkout, handled gracefully (Step 41): a ₹5000 cart against a ₹500 per-transaction
cap gets denied with a clear message, the cart is preserved, and no Razorpay call is made.

Uses a scratch merchant_id ("demo-denial-scenario") rather than the shared "demo" merchant, so
running this script never mutates the live demo merchant's real policy.

Run with: python -m scripts.demo_denial
Then replay the printed trace_id with: python -m scripts.replay_audit <trace_id> --verbose
"""

import asyncio
import uuid

from app.agents.cart_manager import add_item, compute_total
from app.agents.checkout import checkout_node
from app.agents.state import AgentState, empty_cart
from app.audit.logger import AuditLogger
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.integrations.razorpay_client import RazorpayClient
from app.integrations.session_store import SessionStore
from app.logging_config import configure_logging, get_logger
from app.models.merchant_config import get_or_create_config

logger = get_logger("demo_denial")

SCRATCH_MERCHANT_ID = "demo-denial-scenario"


async def main() -> None:
    configure_logging()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        config = await get_or_create_config(session, SCRATCH_MERCHANT_ID)
        config.per_transaction_cap_paise = 50_000  # ₹500
        await session.commit()

    audit = AuditLogger(async_session_factory)
    audit.start()
    session_store = SessionStore()
    razorpay_client = RazorpayClient()

    session_id = f"demo-denial-{uuid.uuid4().hex[:8]}"
    trace_id = str(uuid.uuid4())
    cart = add_item(empty_cart(), "ELEC-006", "27-inch 4K Monitor", 500_000, "electronics", qty=1)  # ₹5000

    state: AgentState = {
        "session_id": session_id,
        "trace_id": trace_id,
        "messages": [{"role": "user", "content": "checkout"}],
        "cart": cart,
    }

    result = await checkout_node(
        state, audit=audit, session_factory=async_session_factory, session_store=session_store,
        razorpay_client=razorpay_client, merchant_id=SCRATCH_MERCHANT_ID,
    )

    print(f"trace_id: {trace_id}")
    print(f"final_response: {result['final_response']}")
    print(f"cart preserved (total still ₹{compute_total(cart) / 100:.2f}): {'cart' not in result}")
    print(f"\nReplay with: python -m scripts.replay_audit {trace_id} --verbose")

    await audit.stop()


if __name__ == "__main__":
    asyncio.run(main())
