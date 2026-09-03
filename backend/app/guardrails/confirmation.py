"""Human-in-the-loop confirmation gate. Layer 3 (guardrails), invariant #8: when the policy engine
gates an action, the agent cannot talk its way past it — the flow pauses for a real merchant decision
and the LLM is never given a tool that skips this wait.

Resolution is awaited via an in-memory asyncio.Event registry (this is a single-process demo app),
not database polling. A separate Redis pub/sub notification lets the merchant console (Layer 1) learn
about new approvals in real time — see app/api/ws.py.
"""

import asyncio
import uuid
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.session_store import SessionStore
from app.logging_config import get_logger
from app.models.pending_approval import ApprovalStatus, PendingApproval
from app.models.policy import ProposedAction
from app.utils.ids import to_uuid

logger = get_logger("confirmation")

MERCHANT_APPROVAL_CHANNEL_TEMPLATE = "merchant:{merchant_id}:approvals"


class ApprovalResult(BaseModel):
    approval_id: uuid.UUID
    status: Literal["approved", "denied", "timed_out"]
    resolved_by: str | None = None

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class ApprovalRegistry:
    """Per-process registry of asyncio.Events so request_approval() can await a resolution that
    happens in a different request handler (the merchant's approve/deny call)."""

    def __init__(self) -> None:
        self._events: dict[uuid.UUID, asyncio.Event] = {}
        self._resolutions: dict[uuid.UUID, tuple[str, str | None]] = {}

    def register(self, approval_id: uuid.UUID) -> asyncio.Event:
        event = asyncio.Event()
        self._events[approval_id] = event
        return event

    def resolve(self, approval_id: uuid.UUID, status: str, resolved_by: str | None) -> bool:
        event = self._events.get(approval_id)
        if event is None:
            return False
        self._resolutions[approval_id] = (status, resolved_by)
        event.set()
        return True

    def pop_resolution(self, approval_id: uuid.UUID) -> tuple[str, str | None] | None:
        self._events.pop(approval_id, None)
        return self._resolutions.pop(approval_id, None)


_registry = ApprovalRegistry()


def get_registry() -> ApprovalRegistry:
    return _registry


async def _mark_status(
    session_factory: async_sessionmaker[AsyncSession], approval_id: uuid.UUID, status: str, resolved_by: str | None
) -> None:
    from datetime import datetime, timezone

    async with session_factory() as db_session:
        result = await db_session.execute(
            select(PendingApproval).where(PendingApproval.id == approval_id)
        )
        approval = result.scalar_one_or_none()
        if approval is None:
            return
        approval.status = status
        approval.resolved_at = datetime.now(timezone.utc)
        approval.resolved_by = resolved_by
        await db_session.commit()


async def request_approval(
    action: ProposedAction,
    trace_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    merchant_id: str = "demo",
    timeout_seconds: float = 300,
    session_store: SessionStore | None = None,
    registry: ApprovalRegistry | None = None,
) -> ApprovalResult:
    registry = registry or _registry

    async with session_factory() as db_session:
        approval = PendingApproval(
            trace_id=trace_id,
            session_id=to_uuid(action.session_id),
            action_type=action.action_type,
            payload=action.model_dump(),
            status=ApprovalStatus.PENDING.value,
        )
        db_session.add(approval)
        await db_session.commit()
        await db_session.refresh(approval)

    event = registry.register(approval.id)

    if session_store is not None:
        await session_store.publish(
            MERCHANT_APPROVAL_CHANNEL_TEMPLATE.format(merchant_id=merchant_id),
            {
                "type": "approval.created",
                "approval_id": str(approval.id),
                "trace_id": str(trace_id),
                "action_type": action.action_type,
                "amount_paise": action.amount_paise,
            },
        )

    logger.info("confirmation.pending_created", approval_id=str(approval.id), trace_id=str(trace_id))

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        await _mark_status(session_factory, approval.id, ApprovalStatus.TIMED_OUT.value, None)
        registry.pop_resolution(approval.id)
        logger.warning("confirmation.timed_out", approval_id=str(approval.id))
        return ApprovalResult(approval_id=approval.id, status="timed_out", resolved_by=None)

    resolution = registry.pop_resolution(approval.id)
    assert resolution is not None
    status, resolved_by = resolution
    return ApprovalResult(approval_id=approval.id, status=status, resolved_by=resolved_by)  # type: ignore[arg-type]


async def resolve_approval(
    approval_id: uuid.UUID,
    approve: bool,
    resolved_by: str,
    session_factory: async_sessionmaker[AsyncSession],
    registry: ApprovalRegistry | None = None,
) -> bool:
    """Called from the merchant console API. Returns False if no one is waiting on this approval
    (already resolved, timed out, or an unknown id)."""
    registry = registry or _registry
    status = ApprovalStatus.APPROVED.value if approve else ApprovalStatus.DENIED.value

    await _mark_status(session_factory, approval_id, status, resolved_by)
    return registry.resolve(approval_id, status, resolved_by)
