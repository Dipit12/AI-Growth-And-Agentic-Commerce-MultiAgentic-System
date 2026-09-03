"""Pure policy evaluation — no I/O, no LLM (invariant #7 in CLAUDE.md: LLMs never decide policy).

Rule order matters and is fixed: category block → payment-method allowlist → per-session cap →
per-transaction cap → auto-approve threshold → allow. The first deny wins; if nothing denies and
the amount exceeds the auto-approve threshold, the action is gated for human approval.
"""

from app.models.policy import MerchantPolicy, PolicyDecision, ProposedAction

RULE_BLOCKED_CATEGORY = "blocked_category"
RULE_PAYMENT_METHOD_NOT_ALLOWED = "payment_method_not_allowed"
RULE_SESSION_CAP_EXCEEDED = "session_cap_exceeded"
RULE_TRANSACTION_CAP_EXCEEDED = "transaction_cap_exceeded"
RULE_AUTO_APPROVE_THRESHOLD = "auto_approve_threshold_exceeded"
RULE_WITHIN_POLICY = "within_policy"


def evaluate(
    action: ProposedAction,
    merchant_config: MerchantPolicy,
    session_spend_so_far: int,
) -> PolicyDecision:
    if action.category in merchant_config.blocked_categories:
        return PolicyDecision(
            verdict="deny",
            reason=f"Category '{action.category}' is blocked by merchant policy.",
            matched_rules=[RULE_BLOCKED_CATEGORY],
        )

    if action.payment_method not in merchant_config.allowed_payment_methods:
        return PolicyDecision(
            verdict="deny",
            reason=f"Payment method '{action.payment_method}' is not accepted by this merchant.",
            matched_rules=[RULE_PAYMENT_METHOD_NOT_ALLOWED],
        )

    if session_spend_so_far + action.amount_paise > merchant_config.per_session_cap_paise:
        return PolicyDecision(
            verdict="deny",
            reason=(
                "This would exceed the per-session spending cap set by the merchant "
                f"(₹{merchant_config.per_session_cap_paise / 100:.2f})."
            ),
            matched_rules=[RULE_SESSION_CAP_EXCEEDED],
        )

    if action.amount_paise > merchant_config.per_transaction_cap_paise:
        return PolicyDecision(
            verdict="deny",
            reason=(
                "This exceeds the per-transaction limit set by the merchant "
                f"(₹{merchant_config.per_transaction_cap_paise / 100:.2f}). You can reduce the cart "
                "or contact the merchant to increase your limit."
            ),
            matched_rules=[RULE_TRANSACTION_CAP_EXCEEDED],
        )

    if action.amount_paise > merchant_config.auto_approve_threshold_paise:
        return PolicyDecision(
            verdict="gate",
            reason=(
                "This exceeds the auto-approve threshold "
                f"(₹{merchant_config.auto_approve_threshold_paise / 100:.2f}) and requires merchant approval."
            ),
            matched_rules=[RULE_AUTO_APPROVE_THRESHOLD],
        )

    return PolicyDecision(
        verdict="allow",
        reason="Within all merchant policy limits.",
        matched_rules=[RULE_WITHIN_POLICY],
    )
