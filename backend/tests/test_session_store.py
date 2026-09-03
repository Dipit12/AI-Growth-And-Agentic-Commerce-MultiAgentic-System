"""Unit tests for SessionStore against fakeredis — cart, messages, and spend counters."""

import pytest

from app.integrations.session_store import SessionStore


@pytest.mark.asyncio
async def test_cart_roundtrip(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(redis=fake_redis)
    session_id = "s1"

    assert await store.get_cart(session_id) == {"items": []}

    cart = {"items": [{"sku": "ELEC-001", "qty": 1}]}
    await store.set_cart(session_id, cart)
    assert await store.get_cart(session_id) == cart


@pytest.mark.asyncio
async def test_message_history_appends_in_order(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(redis=fake_redis)
    session_id = "s2"

    await store.append_message(session_id, {"role": "user", "content": "hi"})
    await store.append_message(session_id, {"role": "assistant", "content": "hello"})

    messages = await store.get_messages(session_id)
    assert [m["content"] for m in messages] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_session_spend_accumulates(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(redis=fake_redis)
    session_id = "s3"

    assert await store.get_session_spend(session_id) == 0

    await store.incr_session_spend(session_id, 10000)
    total = await store.incr_session_spend(session_id, 5000)

    assert total == 15000
    assert await store.get_session_spend(session_id) == 15000


@pytest.mark.asyncio
async def test_session_keys_have_ttl(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(redis=fake_redis)
    session_id = "s4"

    await store.set_cart(session_id, {"items": []})
    ttl = await fake_redis.ttl(f"session:{session_id}:cart")
    assert ttl > 0
