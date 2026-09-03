"""Invariant #4: money events are persisted before the caller proceeds; non-money events are queued."""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit.logger import AuditLogger
from app.audit.models import Actor, AuditEvent
from app.db.base import Base


@pytest_asyncio.fixture
async def session_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


async def _count_events(session_factory: async_sessionmaker) -> int:
    async with session_factory() as session:
        result = await session.execute(select(AuditEvent))
        return len(result.scalars().all())


@pytest.mark.asyncio
async def test_money_event_is_persisted_before_call_returns(session_factory) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_id = uuid.uuid4()

    await audit.log_razorpay_event(
        event_type="razorpay.order.created", trace_id=uuid.uuid4(), session_id=session_id,
    )

    # No drain task running at all — if this were queued it would never land. It must already be there.
    assert await _count_events(session_factory) == 1


@pytest.mark.asyncio
async def test_checkout_actor_event_is_treated_as_money_even_without_keyword(session_factory) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    await audit.log_agent_event(
        actor=Actor.CHECKOUT,
        event_type="checkout.intent_declared",
        trace_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )
    assert await _count_events(session_factory) == 1


@pytest.mark.asyncio
async def test_non_money_event_is_queued_and_drained(session_factory) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    audit.start()
    try:
        await audit.log_agent_event(
            actor=Actor.DISCOVERY,
            event_type="agent.discovery.search",
            trace_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        )
        await audit._queue.join()
        assert await _count_events(session_factory) == 1
    finally:
        await audit.stop()


@pytest.mark.asyncio
async def test_queue_overflow_drops_non_money_events_without_blocking(session_factory) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory, queue_maxsize=1)
    # Fill the queue without draining it.
    audit._queue.put_nowait(
        AuditEvent(
            trace_id=uuid.uuid4(), session_id=uuid.uuid4(), actor=Actor.DISCOVERY.value,
            event_type="filler", payload={},
        )
    )

    result = await asyncio.wait_for(
        audit.log_agent_event(
            actor=Actor.DISCOVERY,
            event_type="agent.discovery.search",
            trace_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
        ),
        timeout=1.0,
    )
    assert result is not None  # returned immediately instead of blocking


@pytest.mark.asyncio
async def test_missing_trace_id_is_generated_with_warning(session_factory) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    event = await audit.log_razorpay_event(
        event_type="razorpay.order.created", trace_id=None, session_id=uuid.uuid4(),
    )
    assert event.trace_id is not None
