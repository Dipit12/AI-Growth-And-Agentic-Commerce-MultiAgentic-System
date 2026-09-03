"""Pydantic types for the policy engine. Layer 3 (guardrails) — pure data, no I/O, no SQLAlchemy."""

from typing import Literal

from pydantic import BaseModel, Field


class ProposedAction(BaseModel):
    action_type: Literal["create_order", "capture_payment", "create_payment_link", "refund"]
    amount_paise: int = Field(ge=0)
    category: str
    payment_method: str
    session_id: str
    cart_hash: str


class PolicyDecision(BaseModel):
    verdict: Literal["allow", "gate", "deny"]
    reason: str
    matched_rules: list[str] = Field(default_factory=list)


class MerchantPolicy(BaseModel):
    """The subset of MerchantConfig the (pure) policy engine needs. Kept separate from the
    SQLAlchemy model so `evaluate()` never has to import anything DB-shaped."""

    merchant_id: str
    per_session_cap_paise: int
    per_transaction_cap_paise: int
    allowed_payment_methods: list[str]
    blocked_categories: list[str]
    auto_approve_threshold_paise: int
