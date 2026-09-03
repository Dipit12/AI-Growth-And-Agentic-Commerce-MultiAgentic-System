"""Integration tests for guardrails.gate_money_action against a mocked Razorpay call — Step 21."""

import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit.logger import AuditLogger
from app.audit.models import AuditEvent
from app.db.base import Base
from app.guardrails.confirmation import ApprovalRegistry, resolve_approval
from app.guardrails.gate import gate_money_action
from app.guardrails.idempotency import RetryExhaustedError
from app.integrations.session_store import SessionStore
from app.models.policy import ProposedAction


@pytest_asyncio.fixture
async def session_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def fake_redis():  # type: ignore[no-untyped-def]
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield redis
    finally:
        await redis.aclose()


def _cheap_action(session_id: str = "s1") -> ProposedAction:
    return ProposedAction(
        action_type="create_order", amount_paise=100_000, category="electronics",
        payment_method="card", session_id=session_id, cart_hash="hash1",
    )


def _gated_action(session_id: str = "s2") -> ProposedAction:
    return ProposedAction(
        action_type="create_order", amount_paise=300_000, category="electronics",
        payment_method="card", session_id=session_id, cart_hash="hash2",
    )


def _expensive_action(session_id: str = "s3") -> ProposedAction:
    return ProposedAction(
        action_type="create_order", amount_paise=999_999_999, category="electronics",
        payment_method="card", session_id=session_id, cart_hash="hash3",
    )


@pytest.mark.asyncio
async def test_happy_path_auto_allow_writes_every_expected_audit_event(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()

    called_with: dict[str, Any] = {}

    async def mock_razorpay_call(idempotency_key: str) -> dict[str, Any]:
        called_with["idempotency_key"] = idempotency_key
        return {"id": "order_mock123", "status": "created"}

    result = await gate_money_action(
        _cheap_action(), trace_id, session_id, mock_razorpay_call,
        audit=audit, session_factory=session_factory, session_store=session_store,
    )

    assert result.success is True
    assert result.verdict == "allow"
    assert result.response == {"id": "order_mock123", "status": "created"}
    assert called_with["idempotency_key"] == result.idempotency_key

    async with session_factory() as session:
        events = (await session.execute(select(AuditEvent).where(AuditEvent.trace_id == trace_id))).scalars().all()
    event_types = {e.event_type for e in events}
    assert "guardrail.intent_declared" in event_types
    assert "guardrail.policy.allow" in event_types
    assert "razorpay.create_order.succeeded" in event_types
    assert len(events) == len(result.audit_event_ids)


@pytest.mark.asyncio
async def test_denied_action_never_calls_razorpay(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()

    calls = {"count": 0}

    async def mock_razorpay_call(idempotency_key: str) -> dict[str, Any]:
        calls["count"] += 1
        return {"id": "should_not_happen"}

    result = await gate_money_action(
        _expensive_action(), trace_id, session_id, mock_razorpay_call,
        audit=audit, session_factory=session_factory, session_store=session_store,
    )

    assert result.success is False
    assert result.verdict == "deny"
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_gated_action_waits_for_approval_then_calls_razorpay(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()
    registry = ApprovalRegistry()

    async def mock_razorpay_call(idempotency_key: str) -> dict[str, Any]:
        return {"id": "order_mock_gated"}

    async def approve_soon() -> None:
        for _ in range(50):
            await asyncio.sleep(0.02)
            if registry._events:
                approval_id = next(iter(registry._events.keys()))
                await resolve_approval(approval_id, approve=True, resolved_by="merchant@demo", session_factory=session_factory, registry=registry)
                return

    import app.guardrails.gate as gate_module
    original_request_approval = gate_module.request_approval

    async def patched_request_approval(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["registry"] = registry
        return await original_request_approval(*args, **kwargs)

    gate_module.request_approval = patched_request_approval
    try:
        approver_task = asyncio.create_task(approve_soon())
        result = await gate_money_action(
            _gated_action(), trace_id, session_id, mock_razorpay_call,
            audit=audit, session_factory=session_factory, session_store=session_store,
        )
        await approver_task
    finally:
        gate_module.request_approval = original_request_approval

    assert result.success is True
    assert result.verdict == "gate"
    assert result.reason_trace.approval_source == "human"


@pytest.mark.asyncio
async def test_retry_exhausted_raises_and_logs_failure_event(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()

    async def always_times_out(idempotency_key: str) -> dict[str, Any]:
        raise httpx.TimeoutException("simulated")

    with pytest.raises(RetryExhaustedError):
        await gate_money_action(
            _cheap_action(session_id="s4"), trace_id, session_id, always_times_out,
            audit=audit, session_factory=session_factory, session_store=session_store,
        )

    async with session_factory() as session:
        events = (await session.execute(select(AuditEvent).where(AuditEvent.trace_id == trace_id))).scalars().all()
    assert any(e.event_type == "razorpay.retry_exhausted" for e in events)


@pytest.mark.asyncio
async def test_idempotency_key_is_stable_across_calls_with_same_inputs(session_factory, fake_redis) -> None:  # type: ignore[no-untyped-def]
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=fake_redis)

    async def mock_razorpay_call(idempotency_key: str) -> dict[str, Any]:
        return {"id": "order_x"}

    result1 = await gate_money_action(
        _cheap_action(session_id="stable"), uuid.uuid4(), uuid.uuid4(), mock_razorpay_call,
        audit=audit, session_factory=session_factory, session_store=session_store,
    )
    result2 = await gate_money_action(
        _cheap_action(session_id="stable"), uuid.uuid4(), uuid.uuid4(), mock_razorpay_call,
        audit=audit, session_factory=session_factory, session_store=session_store,
    )
    assert result1.idempotency_key == result2.idempotency_key
