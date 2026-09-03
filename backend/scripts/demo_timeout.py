"""Deliberately reproducible payment-timeout demo (Step 40): three timeouts on create_order, then a
successful fallback to a gated payment link — all under one idempotency key, one audit trail, and
no double charge.

Run with: python -m scripts.demo_timeout
Then replay the printed trace_id with: python -m scripts.replay_audit <trace_id> --verbose
"""

import asyncio
import uuid

from app.agents.cart_manager import add_item
from app.agents.checkout import checkout_node
from app.agents.state import AgentState, empty_cart
from app.audit.logger import AuditLogger
from app.config import Settings
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.integrations.razorpay_client import RazorpayClient
from app.integrations.session_store import SessionStore
from app.logging_config import configure_logging, get_logger

logger = get_logger("demo_timeout")


async def main() -> None:
    configure_logging()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # The next 3 calls to create_order raise httpx.TimeoutException before touching the network —
    # exactly enough to exhaust with_retry's default max_attempts=3 and trigger the fallback.
    demo_settings = Settings(SIMULATE_RAZORPAY_TIMEOUT="orders:3")
    razorpay_client = RazorpayClient(settings=demo_settings)

    audit = AuditLogger(async_session_factory)
    audit.start()
    session_store = SessionStore()

    session_id = f"demo-timeout-{uuid.uuid4().hex[:8]}"
    trace_id = str(uuid.uuid4())
    # Priced under the default auto-approve threshold (₹2000) so this demo isolates the
    # timeout/retry/fallback behavior without also exercising the merchant-approval gate.
    cart = add_item(empty_cart(), "ELEC-002", "Wireless Mouse", 129_900, "electronics", qty=1)

    state: AgentState = {
        "session_id": session_id,
        "trace_id": trace_id,
        "messages": [{"role": "user", "content": "checkout"}],
        "cart": cart,
    }

    result = await checkout_node(
        state, audit=audit, session_factory=async_session_factory, session_store=session_store,
        razorpay_client=razorpay_client,
    )

    print(f"trace_id: {trace_id}")
    print(f"final_response: {result['final_response']}")
    print(f"cart cleared: {result.get('cart') == {'items': []}}")
    print(f"\nReplay with: python -m scripts.replay_audit {trace_id} --verbose")

    await audit.stop()


if __name__ == "__main__":
    asyncio.run(main())
