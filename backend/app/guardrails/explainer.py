"""Deterministic reason-trace builder. Layer 3 (guardrails) — no LLM involved (invariant #7):
same inputs always produce the same human-readable rationale attached to every money event.
"""

from typing import Literal

from pydantic import BaseModel

from app.guardrails.confirmation import ApprovalResult
from app.models.policy import PolicyDecision, ProposedAction


class ReasonTrace(BaseModel):
    summary: str
    rules_matched: list[str]
    session_spend_before: int
    session_spend_after: int
    approval_source: Literal["auto", "human", "denied"]
    idempotency_key: str


def build_trace(
    action: ProposedAction,
    policy_decision: PolicyDecision,
    approval_result: ApprovalResult | None,
    session_spend_before: int,
    idempotency_key: str,
) -> ReasonTrace:
    if policy_decision.verdict == "deny":
        return ReasonTrace(
            summary=f"Denied by policy: {policy_decision.reason}",
            rules_matched=policy_decision.matched_rules,
            session_spend_before=session_spend_before,
            session_spend_after=session_spend_before,
            approval_source="denied",
            idempotency_key=idempotency_key,
        )

    if policy_decision.verdict == "gate":
        if approval_result is not None and approval_result.approved:
            return ReasonTrace(
                summary=(
                    f"Gated ({policy_decision.reason}) and approved by merchant "
                    f"({approval_result.resolved_by or 'unknown'})."
                ),
                rules_matched=policy_decision.matched_rules,
                session_spend_before=session_spend_before,
                session_spend_after=session_spend_before + action.amount_paise,
                approval_source="human",
                idempotency_key=idempotency_key,
            )

        outcome = "timed out waiting for" if approval_result and approval_result.status == "timed_out" else "was denied by"
        return ReasonTrace(
            summary=f"Gated ({policy_decision.reason}) and {outcome} merchant approval.",
            rules_matched=policy_decision.matched_rules,
            session_spend_before=session_spend_before,
            session_spend_after=session_spend_before,
            approval_source="denied",
            idempotency_key=idempotency_key,
        )

    return ReasonTrace(
        summary=f"Auto-approved: {policy_decision.reason}",
        rules_matched=policy_decision.matched_rules,
        session_spend_before=session_spend_before,
        session_spend_after=session_spend_before + action.amount_paise,
        approval_source="auto",
        idempotency_key=idempotency_key,
    )
