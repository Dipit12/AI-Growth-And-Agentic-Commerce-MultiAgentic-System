"""One test per rule in app/guardrails/policy_engine.py — both the triggering and the passing case."""

import pytest

from app.guardrails.policy_engine import (
    RULE_AUTO_APPROVE_THRESHOLD,
    RULE_BLOCKED_CATEGORY,
    RULE_PAYMENT_METHOD_NOT_ALLOWED,
    RULE_SESSION_CAP_EXCEEDED,
    RULE_TRANSACTION_CAP_EXCEEDED,
    RULE_WITHIN_POLICY,
    evaluate,
)
from app.models.policy import MerchantPolicy, ProposedAction

DEFAULT_POLICY = MerchantPolicy(
    merchant_id="demo",
    per_session_cap_paise=1_000_000,
    per_transaction_cap_paise=500_000,
    allowed_payment_methods=["card", "upi"],
    blocked_categories=["weapons"],
    auto_approve_threshold_paise=200_000,
)


def _action(**overrides: object) -> ProposedAction:
    defaults: dict[str, object] = {
        "action_type": "create_order",
        "amount_paise": 100_000,
        "category": "electronics",
        "payment_method": "card",
        "session_id": "s1",
        "cart_hash": "hash1",
    }
    defaults.update(overrides)
    return ProposedAction(**defaults)  # type: ignore[arg-type]


def test_blocked_category_is_denied() -> None:
    decision = evaluate(_action(category="weapons"), DEFAULT_POLICY, session_spend_so_far=0)
    assert decision.verdict == "deny"
    assert RULE_BLOCKED_CATEGORY in decision.matched_rules


def test_allowed_category_passes_that_rule() -> None:
    decision = evaluate(_action(category="electronics"), DEFAULT_POLICY, session_spend_so_far=0)
    assert RULE_BLOCKED_CATEGORY not in decision.matched_rules


def test_disallowed_payment_method_is_denied() -> None:
    decision = evaluate(_action(payment_method="crypto"), DEFAULT_POLICY, session_spend_so_far=0)
    assert decision.verdict == "deny"
    assert RULE_PAYMENT_METHOD_NOT_ALLOWED in decision.matched_rules


def test_allowed_payment_method_passes_that_rule() -> None:
    decision = evaluate(_action(payment_method="upi"), DEFAULT_POLICY, session_spend_so_far=0)
    assert RULE_PAYMENT_METHOD_NOT_ALLOWED not in decision.matched_rules


def test_session_cap_exceeded_is_denied() -> None:
    decision = evaluate(
        _action(amount_paise=100_000), DEFAULT_POLICY, session_spend_so_far=950_000
    )
    assert decision.verdict == "deny"
    assert RULE_SESSION_CAP_EXCEEDED in decision.matched_rules


def test_within_session_cap_passes_that_rule() -> None:
    decision = evaluate(
        _action(amount_paise=50_000), DEFAULT_POLICY, session_spend_so_far=100_000
    )
    assert RULE_SESSION_CAP_EXCEEDED not in decision.matched_rules


def test_transaction_cap_exceeded_is_denied() -> None:
    decision = evaluate(_action(amount_paise=600_000), DEFAULT_POLICY, session_spend_so_far=0)
    assert decision.verdict == "deny"
    assert RULE_TRANSACTION_CAP_EXCEEDED in decision.matched_rules


def test_within_transaction_cap_passes_that_rule() -> None:
    decision = evaluate(_action(amount_paise=400_000), DEFAULT_POLICY, session_spend_so_far=0)
    assert RULE_TRANSACTION_CAP_EXCEEDED not in decision.matched_rules


def test_above_auto_approve_threshold_is_gated() -> None:
    decision = evaluate(_action(amount_paise=300_000), DEFAULT_POLICY, session_spend_so_far=0)
    assert decision.verdict == "gate"
    assert RULE_AUTO_APPROVE_THRESHOLD in decision.matched_rules


def test_below_auto_approve_threshold_is_allowed() -> None:
    decision = evaluate(_action(amount_paise=100_000), DEFAULT_POLICY, session_spend_so_far=0)
    assert decision.verdict == "allow"
    assert RULE_WITHIN_POLICY in decision.matched_rules


def test_rule_precedence_category_beats_everything_else() -> None:
    # Blocked category AND disallowed payment method AND over every cap — category must win first.
    decision = evaluate(
        _action(category="weapons", payment_method="crypto", amount_paise=999_999_999),
        DEFAULT_POLICY,
        session_spend_so_far=0,
    )
    assert decision.matched_rules == [RULE_BLOCKED_CATEGORY]


@pytest.mark.parametrize("amount", [0, 1, 200_000])
def test_boundary_at_auto_approve_threshold_is_allowed_not_gated(amount: int) -> None:
    decision = evaluate(_action(amount_paise=amount), DEFAULT_POLICY, session_spend_so_far=0)
    assert decision.verdict == "allow"
