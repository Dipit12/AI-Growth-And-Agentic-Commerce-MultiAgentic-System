"""Async Redis wrapper for cart state, conversation memory, and per-session spend counters.
Layer 4 (integrations). Keys are namespaced `session:{session_id}:...` with a 24h TTL.
"""

import json
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

SESSION_TTL_SECONDS = 24 * 60 * 60


def _key(session_id: str, suffix: str) -> str:
    return f"session:{session_id}:{suffix}"


class SessionStore:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis or Redis.from_url(get_settings().REDIS_URL, decode_responses=True)

    async def get_cart(self, session_id: str) -> dict[str, Any]:
        raw = await self._redis.get(_key(session_id, "cart"))
        if raw is None:
            return {"items": []}
        result: dict[str, Any] = json.loads(raw)
        return result

    async def set_cart(self, session_id: str, cart: dict[str, Any]) -> None:
        await self._redis.set(_key(session_id, "cart"), json.dumps(cart), ex=SESSION_TTL_SECONDS)

    async def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        key = _key(session_id, "messages")
        await self._redis.rpush(key, json.dumps(message))
        await self._redis.expire(key, SESSION_TTL_SECONDS)

    async def get_messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        key = _key(session_id, "messages")
        raw_messages = await self._redis.lrange(key, -limit, -1)
        return [json.loads(m) for m in raw_messages]

    async def incr_session_spend(self, session_id: str, amount_paise: int) -> int:
        key = _key(session_id, "spend_paise")
        new_total = await self._redis.incrby(key, amount_paise)
        await self._redis.expire(key, SESSION_TTL_SECONDS)
        return int(new_total)

    async def get_session_spend(self, session_id: str) -> int:
        raw = await self._redis.get(_key(session_id, "spend_paise"))
        return int(raw) if raw is not None else 0

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        """General-purpose pub/sub publish, used by the confirmation gate to notify the merchant
        console WebSocket (app/api/ws.py) of new/resolved approvals in real time."""
        await self._redis.publish(channel, json.dumps(message))

    def pubsub(self) -> Any:
        return self._redis.pubsub()
