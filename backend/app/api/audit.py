"""GET /audit/{trace_id} — fetch a trace's full event timeline for the audit viewer. Layer 1 (surfaces)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEvent
from app.db.session import get_session
from app.models.audit import AuditEventRead, redact_payload

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/{trace_id}", response_model=list[AuditEventRead])
async def get_audit_trail(
    trace_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventRead]:
    result = await session.execute(
        select(AuditEvent)
        .where(AuditEvent.trace_id == trace_id)
        .order_by(AuditEvent.timestamp.asc())
        .limit(limit)
        .offset(offset)
    )
    events = result.scalars().all()
    return [
        AuditEventRead(
            id=event.id,
            trace_id=event.trace_id,
            session_id=event.session_id,
            timestamp=event.timestamp,
            actor=event.actor,
            event_type=event.event_type,
            payload=redact_payload(event.payload),
            reason=event.reason,
        )
        for event in events
    ]
