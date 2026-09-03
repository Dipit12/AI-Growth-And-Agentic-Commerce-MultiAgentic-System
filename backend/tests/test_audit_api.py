"""GET /audit/{trace_id} — ordering, pagination, and redaction (Step 15)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.audit.models import Actor, AuditEvent
from app.db.session import async_session_factory


async def _insert_events(trace_id: uuid.UUID, session_id: uuid.UUID, event_types: list[str], payload_by_index: dict[int, dict] | None = None) -> None:
    payload_by_index = payload_by_index or {}
    base = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        for i, event_type in enumerate(event_types):
            session.add(
                AuditEvent(
                    trace_id=trace_id, session_id=session_id, actor=Actor.ROUTER.value,
                    event_type=event_type, payload=payload_by_index.get(i, {}),
                    # Explicit, strictly-increasing timestamps — SQLite's CURRENT_TIMESTAMP has only
                    # second resolution, which would otherwise make ordering non-deterministic here.
                    timestamp=base + timedelta(milliseconds=i),
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_audit_trail_returns_events_in_timestamp_order(app_client: AsyncClient) -> None:
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()
    await _insert_events(trace_id, session_id, ["first", "second", "third"])

    response = await app_client.get(f"/audit/{trace_id}")
    assert response.status_code == 200
    assert [e["event_type"] for e in response.json()] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_audit_trail_redacts_sensitive_fields(app_client: AsyncClient) -> None:
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()
    await _insert_events(
        trace_id, session_id, ["razorpay.order.created"],
        payload_by_index={0: {"card_number": "4111111111111111", "amount": 1000}},
    )

    response = await app_client.get(f"/audit/{trace_id}")
    body = response.json()
    assert body[0]["payload"]["card_number"] == "***"
    assert body[0]["payload"]["amount"] == 1000


@pytest.mark.asyncio
async def test_audit_trail_pagination(app_client: AsyncClient) -> None:
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()
    await _insert_events(trace_id, session_id, [f"event-{i}" for i in range(5)])

    response = await app_client.get(f"/audit/{trace_id}", params={"limit": 2, "offset": 1})
    body = response.json()
    assert len(body) == 2
    assert body[0]["event_type"] == "event-1"


@pytest.mark.asyncio
async def test_audit_trail_empty_for_unknown_trace(app_client: AsyncClient) -> None:
    response = await app_client.get(f"/audit/{uuid.uuid4()}")
    assert response.status_code == 200
    assert response.json() == []
