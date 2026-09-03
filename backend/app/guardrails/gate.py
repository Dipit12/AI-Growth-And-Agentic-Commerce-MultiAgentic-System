"""The guardrails public API. Layer 3 — this is the ONLY function app/agents/checkout.py imports
from the guardrails package (invariant #2 in CLAUDE.md). It hides policy evaluation, idempotency,
the confirmation gate, retry, and reason-trace assembly behind one call.

Flow: log intent -> generate idempotency key -> evaluate policy -> if gated, request approval ->
if approved/auto-allowed, invoke the Razorpay call with retry -> log outcome + reason trace.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.logger import AuditLogger
from app.guardrails.confirmation import ApprovalResult, request_approval
from app.guardrails.explainer import ReasonTrace, build_trace
from app.guardrails.idempotency import RetryExhaustedError, key_for, with_retry
from app.guardrails.policy_engine import evaluate
from app.integrations.session_store import SessionStore
from app.logging_config import get_logger
from app.models.merchant_config import DEFAULT_MERCHANT_ID, get_or_create_config, to_policy
from app.models.policy import ProposedAction

logger = get_logger("gate")

RazorpayCall = Callable[[str], Awaitable[dict[str, Any]]]


class GateResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    verdict: Literal["allow", "gate", "deny"]
    response: dict[str, Any] | None = None
    reason_trace: ReasonTrace
    idempotency_key: str
    audit_event_ids: list[uuid.UUID] = Field(default_factory=list)


async def gate_money_action(
    action: ProposedAction,
    trace_id: uuid.UUID,
    session_id: uuid.UUID,
    razorpay_call: RazorpayCall,
    *,
    audit: AuditLogger,
    session_factory: async_sessionmaker[AsyncSession],
    session_store: SessionStore,
    merchant_id: str = DEFAULT_MERCHANT_ID,
) -> GateResult:
    """merchant_id lets callers (e.g. demo scripts) target a non-default MerchantConfig row without
    mutating the shared "demo" merchant's live policy."""
    event_ids: list[uuid.UUID] = []

    intent_event = await audit.log_guardrail_event(
        event_type="guardrail.intent_declared",
        trace_id=trace_id,
        session_id=session_id,
        payload=action.model_dump(),
        reason="Checkout agent proposed a money action.",
    )
    event_ids.append(intent_event.id)

    idempotency_key = key_for(action.session_id, action.cart_hash, action.action_type)

    async with session_factory() as db_session:
        config_row = await get_or_create_config(db_session, merchant_id)
        policy = to_policy(config_row)

    session_spend_before = await session_store.get_session_spend(action.session_id)
    decision = evaluate(action, policy, session_spend_before)

    decision_event = await audit.log_guardrail_event(
        event_type=f"guardrail.policy.{decision.verdict}",
        trace_id=trace_id,
        session_id=session_id,
        payload={"matched_rules": decision.matched_rules, "amount_paise": action.amount_paise},
        reason=decision.reason,
    )
    event_ids.append(decision_event.id)

    approval_result: ApprovalResult | None = None

    if decision.verdict == "deny":
        trace = build_trace(action, decision, None, session_spend_before, idempotency_key)
        return GateResult(
            success=False, verdict=decision.verdict, response=None,
            reason_trace=trace, idempotency_key=idempotency_key, audit_event_ids=event_ids,
        )

    if decision.verdict == "gate":
        approval_result = await request_approval(
            action, trace_id, session_factory, merchant_id=merchant_id, session_store=session_store,
        )
        approval_event = await audit.log_human_event(
            event_type=f"guardrail.approval.{approval_result.status}",
            trace_id=trace_id,
            session_id=session_id,
            payload={"approval_id": str(approval_result.approval_id)},
            reason=f"Resolved by {approval_result.resolved_by}" if approval_result.resolved_by else "No merchant response before timeout.",
        )
        event_ids.append(approval_event.id)

        if not approval_result.approved:
            trace = build_trace(action, decision, approval_result, session_spend_before, idempotency_key)
            return GateResult(
                success=False, verdict=decision.verdict, response=None,
                reason_trace=trace, idempotency_key=idempotency_key, audit_event_ids=event_ids,
            )

    wrapped_call = with_retry()(razorpay_call)
    try:
        response = await wrapped_call(idempotency_key)
    except RetryExhaustedError as exc:
        failure_event = await audit.log_razorpay_event(
            event_type="razorpay.retry_exhausted",
            trace_id=trace_id,
            session_id=session_id,
            payload={"action_type": action.action_type, "attempts": exc.attempts},
            reason=str(exc),
        )
        event_ids.append(failure_event.id)
        raise

    await session_store.incr_session_spend(action.session_id, action.amount_paise)
    trace = build_trace(action, decision, approval_result, session_spend_before, idempotency_key)

    success_event = await audit.log_razorpay_event(
        event_type=f"razorpay.{action.action_type}.succeeded",
        trace_id=trace_id,
        session_id=session_id,
        payload={"response": response},
        reason=trace.summary,
    )
    event_ids.append(success_event.id)

    return GateResult(
        success=True, verdict=decision.verdict, response=response,
        reason_trace=trace, idempotency_key=idempotency_key, audit_event_ids=event_ids,
    )
