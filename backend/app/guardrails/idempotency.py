"""Deterministic idempotency-key generation + exponential-backoff retry wrapper. Layer 3 (guardrails),
invariant #3: every Razorpay write carries a key derived from (session_id, cart_hash, action_type),
and a retry after a timeout reproduces the exact same key so Razorpay's own idempotency de-dupes it.
"""

import asyncio
import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

import httpx

from app.logging_config import get_logger

logger = get_logger("idempotency")

# Fixed namespace so key_for() is reproducible across processes and runs.
_IDEMPOTENCY_NAMESPACE = uuid.UUID("6f1b1a3e-2e33-4b7a-9c0e-6a2f9d8b3c11")

P = ParamSpec("P")
T = TypeVar("T")


def key_for(session_id: str, cart_hash: str, action_type: str) -> str:
    """Same inputs always produce the same key (UUIDv5 — deterministic, no randomness)."""
    name = f"{session_id}:{cart_hash}:{action_type}"
    return str(uuid.uuid5(_IDEMPOTENCY_NAMESPACE, name))


class RetryExhaustedError(RuntimeError):
    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"Razorpay call failed after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def with_retry(
    max_attempts: int = 3, base_delay: float = 0.5
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Retries on timeouts, network errors, and Razorpay 5xx. Never retries on 4xx — those are
    permanent rejections (bad request, auth failure) and retrying would just repeat the failure.
    """

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_error: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - re-raised below if not retryable
                    if not _is_retryable(exc):
                        raise
                    last_error = exc
                    logger.warning(
                        "razorpay.retry_attempt",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error=str(exc),
                    )
                    if attempt < max_attempts:
                        await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

            assert last_error is not None
            logger.error("razorpay.retry_exhausted", attempts=max_attempts, error=str(last_error))
            raise RetryExhaustedError(max_attempts, last_error)

        return wrapper

    return decorator
