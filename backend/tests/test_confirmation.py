"""Invariant #8: a gated action pauses for a real merchant decision and cannot be talked past."""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.guardrails.confirmation import ApprovalRegistry, request_approval, resolve_approval
from app.models.policy import ProposedAction


@pytest_asyncio.fixture
async def session_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


def _gated_action() -> ProposedAction:
    return ProposedAction(
        action_type="create_order", amount_paise=300_000, category="electronics",
        payment_method="card", session_id="s1", cart_hash="hash1",
    )


@pytest.mark.asyncio
async def test_approval_resolved_via_api_unblocks_the_waiting_gate(session_factory) -> None:  # type: ignore[no-untyped-def]
    registry = ApprovalRegistry()
    trace_id = uuid.uuid4()

    task = asyncio.create_task(
        request_approval(
            _gated_action(), trace_id, session_factory, timeout_seconds=5, registry=registry,
        )
    )
    await asyncio.sleep(0.05)  # let the pending row get created and the event get registered

    approval_id = next(iter(registry._events.keys()))
    resolved = await resolve_approval(approval_id, approve=True, resolved_by="merchant@demo", session_factory=session_factory, registry=registry)
    assert resolved is True

    result = await task
    assert result.approved is True
    assert result.resolved_by == "merchant@demo"


@pytest.mark.asyncio
async def test_denial_via_api_returns_denied_verdict(session_factory) -> None:  # type: ignore[no-untyped-def]
    registry = ApprovalRegistry()
    trace_id = uuid.uuid4()

    task = asyncio.create_task(
        request_approval(_gated_action(), trace_id, session_factory, timeout_seconds=5, registry=registry)
    )
    await asyncio.sleep(0.05)

    approval_id = next(iter(registry._events.keys()))
    await resolve_approval(approval_id, approve=False, resolved_by="merchant@demo", session_factory=session_factory, registry=registry)

    result = await task
    assert result.approved is False
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_unresolved_approval_times_out_and_denies(session_factory) -> None:  # type: ignore[no-untyped-def]
    registry = ApprovalRegistry()
    trace_id = uuid.uuid4()

    result = await request_approval(
        _gated_action(), trace_id, session_factory, timeout_seconds=0.05, registry=registry,
    )
    assert result.approved is False
    assert result.status == "timed_out"


@pytest.mark.asyncio
async def test_resolving_unknown_approval_id_returns_false(session_factory) -> None:  # type: ignore[no-untyped-def]
    registry = ApprovalRegistry()
    resolved = await resolve_approval(uuid.uuid4(), approve=True, resolved_by="x", session_factory=session_factory, registry=registry)
    assert resolved is False
