"""POST /chat — session/cart continuity, trace_id round-trip to the audit endpoint, and the
gated-action race-to-pending-approval behavior (Step 31). The compiled graph is monkeypatched with a
fake so these tests exercise chat.py's own logic without a real LLM or vector store.
"""

import asyncio

import pytest
from httpx import AsyncClient

from app.agents.cart_manager import add_item
from app.agents.state import empty_cart


class FakeGraph:
    def __init__(self, result: dict | None = None, delay: float = 0.0) -> None:
        self._result = result or {"final_response": "hello!", "cart": empty_cart()}
        self._delay = delay

    async def ainvoke(self, state: dict) -> dict:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._result


@pytest.fixture
def fake_redis_session_store(monkeypatch):  # type: ignore[no-untyped-def]
    import fakeredis.aioredis

    import app.api.deps as deps_module
    from app.integrations.session_store import SessionStore

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    fake_store = SessionStore(redis=redis)
    monkeypatch.setattr(deps_module, "session_store", fake_store)
    return fake_store


@pytest.mark.asyncio
async def test_chat_returns_response_session_and_trace_id(app_client: AsyncClient, fake_redis_session_store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.chat as chat_module

    monkeypatch.setattr(
        chat_module, "chat_graph", FakeGraph({"final_response": "Found some keyboards!", "cart": empty_cart()})
    )

    response = await app_client.post("/chat", json={"message": "show me keyboards"})
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Found some keyboards!"
    assert body["session_id"]
    assert body["trace_id"]
    assert body["pending_approval_id"] is None


@pytest.mark.asyncio
async def test_chat_trace_id_is_retrievable_via_audit_endpoint(app_client: AsyncClient, fake_redis_session_store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.chat as chat_module

    monkeypatch.setattr(chat_module, "chat_graph", FakeGraph())

    response = await app_client.post("/chat", json={"message": "hi"})
    trace_id = response.json()["trace_id"]

    audit_response = await app_client.get(f"/audit/{trace_id}")
    assert audit_response.status_code == 200


@pytest.mark.asyncio
async def test_chat_preserves_session_id_and_cart_across_turns(app_client: AsyncClient, fake_redis_session_store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.chat as chat_module

    cart_after_add = add_item(empty_cart(), "ELEC-001", "Keyboard", 349900, "electronics")
    monkeypatch.setattr(chat_module, "chat_graph", FakeGraph({"final_response": "Added!", "cart": cart_after_add}))

    first = await app_client.post("/chat", json={"message": "add the keyboard"})
    session_id = first.json()["session_id"]
    assert first.json()["cart"]["items"][0]["sku"] == "ELEC-001"

    monkeypatch.setattr(
        chat_module, "chat_graph", FakeGraph({"final_response": "Here is your cart.", "cart": cart_after_add})
    )
    second = await app_client.post("/chat", json={"session_id": session_id, "message": "what's in my cart?"})
    assert second.json()["session_id"] == session_id
    assert second.json()["cart"]["items"][0]["sku"] == "ELEC-001"


@pytest.mark.asyncio
async def test_chat_returns_pending_approval_id_when_gate_is_slow(app_client: AsyncClient, fake_redis_session_store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.chat as chat_module

    monkeypatch.setattr(chat_module, "GATE_RACE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(chat_module, "chat_graph", FakeGraph(delay=1.0))

    response = await app_client.post("/chat", json={"message": "buy the very expensive thing"})
    assert response.status_code == 200
    body = response.json()
    assert "waiting for merchant confirmation" in body["response"].lower()
    # No PendingApproval row exists for this trace_id (the fake graph never created one), so the
    # lookup correctly comes back empty rather than guessing at an id.
    assert body["pending_approval_id"] is None
