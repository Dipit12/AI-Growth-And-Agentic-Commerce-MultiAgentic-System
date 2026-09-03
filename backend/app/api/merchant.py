"""Merchant console API — policy config, pending-approval queue, and approve/deny actions.
Layer 1 (surfaces). Protected by a shared demo API key (X-Merchant-Key header).
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_store
from app.config import get_settings
from app.db.session import get_session
from app.guardrails.confirmation import MERCHANT_APPROVAL_CHANNEL_TEMPLATE, resolve_approval
from app.models.merchant_config import (
    DEFAULT_MERCHANT_ID,
    MerchantConfig,
    MerchantConfigRead,
    MerchantConfigUpdate,
    get_or_create_config,
)
from app.models.pending_approval import PendingApproval, PendingApprovalRead

router = APIRouter(prefix="/merchant", tags=["merchant"])


async def require_merchant_key(x_merchant_key: str = Header(default="")) -> None:
    settings = get_settings()
    if x_merchant_key != settings.MERCHANT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Merchant-Key header.")


@router.get("/{merchant_id}/config", response_model=MerchantConfigRead, dependencies=[Depends(require_merchant_key)])
async def get_merchant_config(
    merchant_id: str, session: AsyncSession = Depends(get_session)
) -> MerchantConfig:
    return await get_or_create_config(session, merchant_id)


@router.put("/{merchant_id}/config", response_model=MerchantConfigRead, dependencies=[Depends(require_merchant_key)])
async def update_merchant_config(
    merchant_id: str,
    update: MerchantConfigUpdate,
    session: AsyncSession = Depends(get_session),
) -> MerchantConfig:
    config = await get_or_create_config(session, merchant_id)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(config, field, value)
    await session.commit()
    await session.refresh(config)
    return config


@router.get(
    "/{merchant_id}/approvals",
    response_model=list[PendingApprovalRead],
    dependencies=[Depends(require_merchant_key)],
)
async def list_approvals(
    merchant_id: str,
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[PendingApproval]:
    query = select(PendingApproval).order_by(PendingApproval.created_at.desc())
    if status is not None:
        query = query.where(PendingApproval.status == status)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/approvals/{approval_id}/approve", dependencies=[Depends(require_merchant_key)])
async def approve_approval(
    approval_id: uuid.UUID,
    x_merchant_key: str = Header(default=""),
) -> dict[str, bool]:
    return await _resolve(approval_id, approve=True, resolved_by="merchant-console")


@router.post("/approvals/{approval_id}/deny", dependencies=[Depends(require_merchant_key)])
async def deny_approval(
    approval_id: uuid.UUID,
    x_merchant_key: str = Header(default=""),
) -> dict[str, bool]:
    return await _resolve(approval_id, approve=False, resolved_by="merchant-console")


async def _resolve(approval_id: uuid.UUID, approve: bool, resolved_by: str) -> dict[str, bool]:
    from app.db.session import async_session_factory

    resolved = await resolve_approval(approval_id, approve, resolved_by, async_session_factory)
    if not resolved:
        raise HTTPException(status_code=404, detail="No pending approval waiting on that id.")

    try:
        session_store = get_session_store()
        await session_store.publish(
            MERCHANT_APPROVAL_CHANNEL_TEMPLATE.format(merchant_id=DEFAULT_MERCHANT_ID),
            {
                "type": "approval.resolved",
                "approval_id": str(approval_id),
                "status": "approved" if approve else "denied",
                "resolved_by": resolved_by,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:  # pragma: no cover - Redis unavailable shouldn't break the approve/deny action
        pass

    return {"resolved": True}
