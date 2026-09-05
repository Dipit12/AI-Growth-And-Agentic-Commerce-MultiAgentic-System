"""Central writer for all audit events. Layer 5 (observability) — the one place every other layer
calls to record what happened. Money events are written synchronously and block their caller until
persisted (invariant #4 in CLAUDE.md); everything else is queued and drained in the background so
audit logging never adds latency to a chat turn.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import Actor, AuditEvent
from app.logging_config import get_logger

logger = get_logger("audit_logger")

## Guardrail and human events are included here (beyond the razorpay/checkout actors) because in
## this system every guardrail decision and every human approval/denial is, by construction, part of
## the pre-call record for a money action (CLAUDE.md invariant #4: "the pre-call record captures
## intent + reasoning + guardrail decision"). Router/discovery/recommender/cart_manager events are
## the only ones that go through the async queue.
_MONEY_ACTORS = {Actor.RAZORPAY.value, Actor.CHECKOUT.value, Actor.GUARDRAIL.value, Actor.HUMAN.value}
_MONEY_KEYWORDS = ("payment", "order", "refund")

DEFAULT_QUEUE_MAXSIZE = 1000


def _is_money_event(actor: str, event_type: str) -> bool:
    if actor in _MONEY_ACTORS:
        return True
    return any(keyword in event_type for keyword in _MONEY_KEYWORDS)


class AuditLogger:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue[AuditEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._drain_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        """Drains whatever is still queued before cancelling the background task — a short-lived
        script that does start()...log a few events...stop() would otherwise lose exactly the
        events it just logged, since put_nowait() returns before the drain loop ever gets a chance
        to run (confirmed: this silently dropped scripts/demo_hallucination.py's discovery events)."""
        if self._drain_task is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=5.0)
            except TimeoutError:
                logger.error("audit.stop_drain_timed_out", remaining=self._queue.qsize())
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

    async def log_agent_event(
        self,
        *,
        actor: Actor,
        event_type: str,
        trace_id: uuid.UUID | None,
        session_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> AuditEvent:
        return await self._log(
            actor=actor, event_type=event_type, trace_id=trace_id, session_id=session_id,
            payload=payload, reason=reason,
        )

    async def log_guardrail_event(
        self,
        *,
        event_type: str,
        trace_id: uuid.UUID | None,
        session_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> AuditEvent:
        return await self._log(
            actor=Actor.GUARDRAIL, event_type=event_type, trace_id=trace_id, session_id=session_id,
            payload=payload, reason=reason,
        )

    async def log_razorpay_event(
        self,
        *,
        event_type: str,
        trace_id: uuid.UUID | None,
        session_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> AuditEvent:
        return await self._log(
            actor=Actor.RAZORPAY, event_type=event_type, trace_id=trace_id, session_id=session_id,
            payload=payload, reason=reason,
        )

    async def log_human_event(
        self,
        *,
        event_type: str,
        trace_id: uuid.UUID | None,
        session_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> AuditEvent:
        return await self._log(
            actor=Actor.HUMAN, event_type=event_type, trace_id=trace_id, session_id=session_id,
            payload=payload, reason=reason,
        )

    async def _log(
        self,
        *,
        actor: Actor,
        event_type: str,
        trace_id: uuid.UUID | None,
        session_id: uuid.UUID,
        payload: dict[str, Any] | None,
        reason: str | None,
    ) -> AuditEvent:
        if trace_id is None:
            trace_id = uuid.uuid4()
            logger.warning("audit.missing_trace_id", generated=str(trace_id), event_type=event_type)

        event = AuditEvent(
            trace_id=trace_id,
            session_id=session_id,
            actor=actor.value,
            event_type=event_type,
            payload=payload or {},
            reason=reason,
        )

        if _is_money_event(actor.value, event_type):
            await self._persist(event)
        else:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.error(
                    "audit.queue_overflow_dropped",
                    event_type=event_type,
                    trace_id=str(trace_id),
                    session_id=str(session_id),
                )

        return event

    async def _persist(self, event: AuditEvent) -> None:
        async with self._session_factory() as session:
            session.add(event)
            await session.commit()

    async def _drain_loop(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._persist(event)
            except Exception:  # pragma: no cover - defensive, never crash the drain loop
                logger.error(
                    "audit.drain_write_failed",
                    event_type=event.event_type,
                    trace_id=str(event.trace_id),
                    exc_info=True,
                )
            finally:
                self._queue.task_done()
