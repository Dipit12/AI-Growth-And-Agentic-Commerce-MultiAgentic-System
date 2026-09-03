"""Human-in-the-loop approval queue. Layer 4 (integrations & data) — written/read by guardrails/confirmation.py."""

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


def _json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ApprovalStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PendingApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    session_id: uuid.UUID
    action_type: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
