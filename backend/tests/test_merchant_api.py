"""Merchant console API — config, the approval queue, and the X-Merchant-Key auth gate (Steps 19 & 32)."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db.session import async_session_factory
from app.guardrails.confirmation import request_approval
from app.models.pending_approval import ApprovalStatus, PendingApproval
from app.models.policy import ProposedAction

HEADERS = {"X-Merchant-Key": get_settings().MERCHANT_API_KEY}


@pytest.mark.asyncio
async def test_missing_merchant_key_is_rejected(app_client: AsyncClient) -> None:
    response = await app_client.get("/merchant/demo/config")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_merchant_key_is_rejected(app_client: AsyncClient) -> None:
    response = await app_client.get("/merchant/demo/config", headers={"X-Merchant-Key": "wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_config_creates_default_row_on_first_access(app_client: AsyncClient) -> None:
    response = await app_client.get(f"/merchant/config-test-{uuid.uuid4()}/config", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["per_transaction_cap_paise"] > 0
    assert body["allowed_payment_methods"]


@pytest.mark.asyncio
async def test_update_config_changes_subsequent_reads(app_client: AsyncClient) -> None:
    merchant_id = f"update-test-{uuid.uuid4()}"
    update_response = await app_client.put(
        f"/merchant/{merchant_id}/config", headers=HEADERS, json={"per_transaction_cap_paise": 12345},
    )
    assert update_response.status_code == 200
    assert update_response.json()["per_transaction_cap_paise"] == 12345

    get_response = await app_client.get(f"/merchant/{merchant_id}/config", headers=HEADERS)
    assert get_response.json()["per_transaction_cap_paise"] == 12345


@pytest.mark.asyncio
async def test_approve_via_api_unblocks_a_waiting_gate(app_client: AsyncClient) -> None:
    action = ProposedAction(
        action_type="create_order", amount_paise=300_000, category="electronics",
        payment_method="card", session_id="api-approve-test", cart_hash="hash-approve",
    )
    trace_id = uuid.uuid4()

    task = asyncio.create_task(request_approval(action, trace_id, async_session_factory, timeout_seconds=5))
    await asyncio.sleep(0.1)

    async with async_session_factory() as session:
        result = await session.execute(select(PendingApproval).where(PendingApproval.trace_id == trace_id))
        approval = result.scalar_one()

    response = await app_client.post(f"/merchant/approvals/{approval.id}/approve", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"resolved": True}

    approval_result = await task
    assert approval_result.approved is True
    assert approval_result.resolved_by == "merchant-console"


@pytest.mark.asyncio
async def test_deny_via_api_unblocks_with_denial(app_client: AsyncClient) -> None:
    action = ProposedAction(
        action_type="create_order", amount_paise=300_000, category="electronics",
        payment_method="card", session_id="api-deny-test", cart_hash="hash-deny",
    )
    trace_id = uuid.uuid4()

    task = asyncio.create_task(request_approval(action, trace_id, async_session_factory, timeout_seconds=5))
    await asyncio.sleep(0.1)

    async with async_session_factory() as session:
        result = await session.execute(select(PendingApproval).where(PendingApproval.trace_id == trace_id))
        approval = result.scalar_one()

    response = await app_client.post(f"/merchant/approvals/{approval.id}/deny", headers=HEADERS)
    assert response.status_code == 200

    approval_result = await task
    assert approval_result.approved is False
    assert approval_result.status == "denied"


@pytest.mark.asyncio
async def test_resolving_unknown_approval_returns_404(app_client: AsyncClient) -> None:
    response = await app_client.post(f"/merchant/approvals/{uuid.uuid4()}/approve", headers=HEADERS)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_approvals_filters_by_status(app_client: AsyncClient) -> None:
    trace_id, session_id = uuid.uuid4(), uuid.uuid4()
    async with async_session_factory() as session:
        session.add(
            PendingApproval(
                trace_id=trace_id, session_id=session_id, action_type="create_order",
                payload={}, status=ApprovalStatus.TIMED_OUT.value,
            )
        )
        await session.commit()

    response = await app_client.get(
        "/merchant/demo/approvals", headers=HEADERS, params={"status": "timed_out"}
    )
    assert response.status_code == 200
    assert any(item["trace_id"] == str(trace_id) for item in response.json())
