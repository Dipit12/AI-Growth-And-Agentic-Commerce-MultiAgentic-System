"""Invariant #3: deterministic idempotency keys + retry-with-backoff on transient failures only."""

import httpx
import pytest

from app.guardrails.idempotency import RetryExhaustedError, key_for, with_retry


def test_key_for_is_deterministic() -> None:
    key1 = key_for("session-1", "cart-hash-abc", "create_order")
    key2 = key_for("session-1", "cart-hash-abc", "create_order")
    assert key1 == key2


def test_key_for_differs_on_any_input_change() -> None:
    base = key_for("session-1", "cart-hash-abc", "create_order")
    assert base != key_for("session-2", "cart-hash-abc", "create_order")
    assert base != key_for("session-1", "cart-hash-xyz", "create_order")
    assert base != key_for("session-1", "cart-hash-abc", "refund")


@pytest.mark.asyncio
async def test_retry_succeeds_after_three_timeouts_within_four_attempts() -> None:
    calls = {"count": 0}

    @with_retry(max_attempts=4, base_delay=0.0)
    async def flaky_call() -> str:
        calls["count"] += 1
        if calls["count"] <= 3:
            raise httpx.TimeoutException("simulated timeout")
        return "ok"

    result = await flaky_call()
    assert result == "ok"
    assert calls["count"] == 4


@pytest.mark.asyncio
async def test_retry_exhausted_raises_after_max_attempts() -> None:
    @with_retry(max_attempts=3, base_delay=0.0)
    async def always_times_out() -> str:
        raise httpx.TimeoutException("simulated timeout")

    with pytest.raises(RetryExhaustedError) as exc_info:
        await always_times_out()
    assert exc_info.value.attempts == 3


@pytest.mark.asyncio
async def test_4xx_error_is_never_retried() -> None:
    calls = {"count": 0}

    @with_retry(max_attempts=5, base_delay=0.0)
    async def bad_request() -> str:
        calls["count"] += 1
        request = httpx.Request("POST", "https://api.razorpay.com/orders")
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await bad_request()
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_5xx_error_is_retried() -> None:
    calls = {"count": 0}

    @with_retry(max_attempts=2, base_delay=0.0)
    async def server_error_then_ok() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            request = httpx.Request("POST", "https://api.razorpay.com/orders")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("bad gateway", request=request, response=response)
        return "ok"

    result = await server_error_then_ok()
    assert result == "ok"
    assert calls["count"] == 2
