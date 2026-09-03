"""Verifies reason-trace text and spend accounting for every approval path."""

import uuid

from app.guardrails.confirmation import ApprovalResult
from app.guardrails.explainer import build_trace
from app.guardrails.policy_engine import RULE_AUTO_APPROVE_THRESHOLD, RULE_TRANSACTION_CAP_EXCEEDED, RULE_WITHIN_POLICY
from app.models.policy import PolicyDecision, ProposedAction

ACTION = ProposedAction(
    action_type="create_order",
    amount_paise=100_000,
    category="electronics",
    payment_method="card",
    session_id="s1",
    cart_hash="hash1",
)


def test_auto_approve_path() -> None:
    decision = PolicyDecision(verdict="allow", reason="Within all merchant policy limits.", matched_rules=[RULE_WITHIN_POLICY])
    trace = build_trace(ACTION, decision, approval_result=None, session_spend_before=0, idempotency_key="k1")

    assert trace.approval_source == "auto"
    assert trace.summary.startswith("Auto-approved")
    assert trace.session_spend_after == 100_000


def test_gated_and_approved_path() -> None:
    decision = PolicyDecision(verdict="gate", reason="Exceeds auto-approve threshold.", matched_rules=[RULE_AUTO_APPROVE_THRESHOLD])
    approval = ApprovalResult(approval_id=uuid.uuid4(), status="approved", resolved_by="merchant@demo")

    trace = build_trace(ACTION, decision, approval_result=approval, session_spend_before=50_000, idempotency_key="k2")

    assert trace.approval_source == "human"
    assert "approved by merchant" in trace.summary
    assert trace.session_spend_after == 150_000


def test_gated_and_denied_path() -> None:
    decision = PolicyDecision(verdict="gate", reason="Exceeds auto-approve threshold.", matched_rules=[RULE_AUTO_APPROVE_THRESHOLD])
    approval = ApprovalResult(approval_id=uuid.uuid4(), status="denied", resolved_by="merchant@demo")

    trace = build_trace(ACTION, decision, approval_result=approval, session_spend_before=50_000, idempotency_key="k3")

    assert trace.approval_source == "denied"
    assert "was denied by" in trace.summary
    assert trace.session_spend_after == 50_000  # unchanged — no charge happened


def test_gated_and_timed_out_path() -> None:
    decision = PolicyDecision(verdict="gate", reason="Exceeds auto-approve threshold.", matched_rules=[RULE_AUTO_APPROVE_THRESHOLD])
    approval = ApprovalResult(approval_id=uuid.uuid4(), status="timed_out", resolved_by=None)

    trace = build_trace(ACTION, decision, approval_result=approval, session_spend_before=0, idempotency_key="k4")

    assert trace.approval_source == "denied"
    assert "timed out waiting for" in trace.summary
    assert trace.session_spend_after == 0


def test_denied_by_policy_path() -> None:
    decision = PolicyDecision(verdict="deny", reason="This exceeds the per-transaction limit.", matched_rules=[RULE_TRANSACTION_CAP_EXCEEDED])

    trace = build_trace(ACTION, decision, approval_result=None, session_spend_before=20_000, idempotency_key="k5")

    assert trace.approval_source == "denied"
    assert trace.summary.startswith("Denied by policy")
    assert trace.session_spend_after == 20_000


def test_trace_is_deterministic_for_identical_inputs() -> None:
    decision = PolicyDecision(verdict="allow", reason="Within all merchant policy limits.", matched_rules=[RULE_WITHIN_POLICY])
    trace_a = build_trace(ACTION, decision, approval_result=None, session_spend_before=0, idempotency_key="k6")
    trace_b = build_trace(ACTION, decision, approval_result=None, session_spend_before=0, idempotency_key="k6")
    assert trace_a == trace_b
