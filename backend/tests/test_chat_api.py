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
async def test_chat_keeps_waiting_when_slow_but_not_actually_gated(app_client: AsyncClient, fake_redis_session_store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A slow turn (e.g. a small local LLM) that never created a PendingApproval row must NOT be
    reported as 'waiting for merchant confirmation' — that message is only true when a checkout was
    genuinely gated. The endpoint should just keep waiting and return the real result."""
    import app.api.chat as chat_module

    monkeypatch.setattr(chat_module, "GATE_RACE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(chat_module, "MAX_TOTAL_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(
        chat_module, "chat_graph", FakeGraph({"final_response": "Slow but real answer.", "cart": empty_cart()}, delay=0.5)
    )

    response = await app_client.post("/chat", json={"message": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Slow but real answer."
    assert body["pending_approval_id"] is None


@pytest.mark.asyncio
async def test_chat_returns_pending_approval_id_when_genuinely_gated(app_client: AsyncClient, fake_redis_session_store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When a PendingApproval row actually exists for the trace_id, the endpoint must return
    promptly with that approval id rather than waiting out the full graph run."""
    import uuid

    import app.api.chat as chat_module
    from app.db.session import async_session_factory
    from app.models.pending_approval import ApprovalStatus, PendingApproval

    monkeypatch.setattr(chat_module, "GATE_RACE_TIMEOUT_SECONDS", 0.2)

    class GatedGraph:
        async def ainvoke(self, state: dict) -> dict:
            # _start_turn always generates a fresh UUID4 session_id when the client sends none.
            async with async_session_factory() as session:
                session.add(
                    PendingApproval(
                        trace_id=uuid.UUID(state["trace_id"]),
                        session_id=uuid.UUID(state["session_id"]),
                        action_type="create_order",
                        payload={},
                        status=ApprovalStatus.PENDING.value,
                    )
                )
                await session.commit()
            await asyncio.sleep(30)  # never resolves within the test
            return {"final_response": "should never get here", "cart": empty_cart()}

    monkeypatch.setattr(chat_module, "chat_graph", GatedGraph())

    response = await app_client.post("/chat", json={"message": "buy the very expensive thing"})
    assert response.status_code == 200
    body = response.json()
    assert "waiting for merchant confirmation" in body["response"].lower()
    assert body["pending_approval_id"] is not None
