"""Append-only audit event table. Layer 5 (observability) — every layer above writes here, nothing updates or deletes."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


def _json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class Actor(str, enum.Enum):
    ROUTER = "router"
    DISCOVERY = "discovery"
    RECOMMENDER = "recommender"
    CART_MANAGER = "cart_manager"
    SUPPORT = "support"
    CHECKOUT = "checkout"
    GUARDRAIL = "guardrail"
    RAZORPAY = "razorpay"
    HUMAN = "human"


class AuditEvent(Base):
    """Append-only: this model deliberately exposes no update/delete helpers. Writers must INSERT only."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_trace_timestamp", "trace_id", "timestamp"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
