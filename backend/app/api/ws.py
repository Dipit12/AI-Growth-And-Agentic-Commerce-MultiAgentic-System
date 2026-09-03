"""WebSocket for real-time merchant approval notifications. Layer 1 (surfaces). Backed by Redis
pub/sub — the same channel guardrails/confirmation.py publishes to when an approval is created or
resolved (see MERCHANT_APPROVAL_CHANNEL_TEMPLATE), not database polling.
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.guardrails.confirmation import MERCHANT_APPROVAL_CHANNEL_TEMPLATE
from app.integrations.session_store import SessionStore
from app.logging_config import get_logger

logger = get_logger("ws")
router = APIRouter(tags=["ws"])


@router.websocket("/ws/merchant/{merchant_id}")
async def merchant_approvals_ws(websocket: WebSocket, merchant_id: str) -> None:
    await websocket.accept()

    session_store = SessionStore()
    pubsub = session_store.pubsub()
    channel = MERCHANT_APPROVAL_CHANNEL_TEMPLATE.format(merchant_id=merchant_id)
    await pubsub.subscribe(channel)

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None:
                data = message["data"]
                await websocket.send_text(data if isinstance(data, str) else data.decode())
            else:
                await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        logger.info("ws.merchant_disconnected", merchant_id=merchant_id)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
