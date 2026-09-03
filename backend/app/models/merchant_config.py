"""Merchant policy configuration. Layer 4 (integrations & data) — read by guardrails/policy_engine.py."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.policy import MerchantPolicy


class MerchantConfig(Base):
    __tablename__ = "merchant_configs"

    merchant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    per_session_cap_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=1_000_000)
    per_transaction_cap_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=500_000)
    allowed_payment_methods: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    blocked_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    auto_approve_threshold_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=200_000)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MerchantConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str
    per_session_cap_paise: int
    per_transaction_cap_paise: int
    allowed_payment_methods: list[str]
    blocked_categories: list[str]
    auto_approve_threshold_paise: int


class MerchantConfigUpdate(BaseModel):
    per_session_cap_paise: int | None = Field(default=None, ge=0)
    per_transaction_cap_paise: int | None = Field(default=None, ge=0)
    allowed_payment_methods: list[str] | None = None
    blocked_categories: list[str] | None = None
    auto_approve_threshold_paise: int | None = Field(default=None, ge=0)


def to_policy(config: MerchantConfig) -> MerchantPolicy:
    """Bridges the persisted SQLAlchemy row into the pure pydantic type policy_engine.evaluate() takes."""
    return MerchantPolicy(
        merchant_id=config.merchant_id,
        per_session_cap_paise=config.per_session_cap_paise,
        per_transaction_cap_paise=config.per_transaction_cap_paise,
        allowed_payment_methods=list(config.allowed_payment_methods),
        blocked_categories=list(config.blocked_categories),
        auto_approve_threshold_paise=config.auto_approve_threshold_paise,
    )


DEFAULT_MERCHANT_ID = "demo"

async def get_or_create_config(session: AsyncSession, merchant_id: str) -> MerchantConfig:
    """Shared by the merchant console API and the guardrails gate — both need the same row."""
    result = await session.execute(select(MerchantConfig).where(MerchantConfig.merchant_id == merchant_id))
    config = result.scalar_one_or_none()
    if config is None:
        defaults = {**DEFAULT_MERCHANT_CONFIG, "merchant_id": merchant_id}
        config = MerchantConfig(**defaults)
        session.add(config)
        await session.commit()
        await session.refresh(config)
    return config


DEFAULT_MERCHANT_CONFIG = {
    "merchant_id": DEFAULT_MERCHANT_ID,
    "per_session_cap_paise": 1_000_000,  # ₹10,000
    "per_transaction_cap_paise": 500_000,  # ₹5,000
    "allowed_payment_methods": ["card", "upi", "netbanking", "wallet"],
    "blocked_categories": [],
    "auto_approve_threshold_paise": 200_000,  # ₹2,000
}
