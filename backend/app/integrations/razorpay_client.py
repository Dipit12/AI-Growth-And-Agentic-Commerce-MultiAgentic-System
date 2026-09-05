"""Async wrapper around the Razorpay SDK. Layer 4 (integrations) — the ONLY module allowed to speak
to Razorpay's write endpoints, and only ever called from app/agents/checkout.py via app/guardrails/gate.py
(see CLAUDE.md invariants #1 and #2).

Test-mode is enforced here, in code, not just config (invariant #5): construction raises unless the
configured key id starts with "rzp_test_". Do not weaken this assertion.
"""

import asyncio
import itertools
from typing import Any

import httpx
import razorpay

from app.config import Settings, get_settings
from app.logging_config import get_logger

logger = get_logger("razorpay_client")

_RETRYABLE_STATUS = {500, 502, 503, 504}


class RazorpayTestModeError(RuntimeError):
    """Raised when a non-test Razorpay key is supplied. This check must never be bypassed."""


class RazorpayAPIError(RuntimeError):
    """Wraps a non-retryable (4xx) Razorpay error."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Razorpay API error {status_code}: {message}")
        self.status_code = status_code


class _TimeoutSimulator:
    """Test/demo seam: SIMULATE_RAZORPAY_TIMEOUT="orders:3" makes the next 3 create_order calls
    raise httpx.TimeoutException before touching the network. Used by scripts/demo_timeout.py and
    tests/test_failure_modes.py to reproduce the timeout-then-fallback scenario deterministically.
    Never read outside this module.
    """

    def __init__(self, spec: str) -> None:
        self._remaining: dict[str, int] = {}
        if spec:
            endpoint, _, count = spec.partition(":")
            self._remaining[endpoint] = int(count) if count else 0

    def maybe_raise(self, endpoint: str) -> None:
        remaining = self._remaining.get(endpoint, 0)
        if remaining > 0:
            self._remaining[endpoint] = remaining - 1
            raise httpx.TimeoutException(f"simulated timeout on {endpoint}")


class RazorpayClient:
    """Async facade over razorpay-python (which is sync) via asyncio.to_thread."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

        if not self._settings.RAZORPAY_KEY_ID.startswith("rzp_test_"):
            raise RazorpayTestModeError(
                "RAZORPAY_KEY_ID must start with 'rzp_test_' — this project is test-mode only. "
                "Refusing to initialize with what looks like a live key."
            )

        self._client = razorpay.Client(
            auth=(self._settings.RAZORPAY_KEY_ID, self._settings.RAZORPAY_KEY_SECRET)
        )
        self._simulator = _TimeoutSimulator(self._settings.SIMULATE_RAZORPAY_TIMEOUT)

    async def create_order(
        self,
        amount_paise: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._simulator.maybe_raise("orders")
        payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "notes": notes,
        }
        return await self._call(
            lambda: self._client.order.create(
                data=payload, headers={"X-Razorpay-Idempotency": idempotency_key}
            ),
            idempotency_key=idempotency_key,
        )

    async def capture_payment(
        self, payment_id: str, amount_paise: int, idempotency_key: str
    ) -> dict[str, Any]:
        self._simulator.maybe_raise("payments")
        return await self._call(
            lambda: self._client.payment.capture(
                payment_id,
                amount_paise,
                headers={"X-Razorpay-Idempotency": idempotency_key},
            ),
            idempotency_key=idempotency_key,
        )

    async def create_payment_link(
        self,
        amount_paise: int,
        description: str,
        customer: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._simulator.maybe_raise("payment_links")
        payload: dict[str, Any] = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "notify": {"sms": False, "email": bool(customer.get("email"))},
        }
        # Razorpay's API rejects an empty/malformed `customer` object outright ("incorrect JSON
        # object received - faulty key: customer") rather than treating it as "no customer info" —
        # confirmed against the live test-mode API. Omit the key entirely when there's nothing real.
        if customer:
            payload["customer"] = customer
        return await self._call(
            lambda: self._client.payment_link.create(
                payload, headers={"X-Razorpay-Idempotency": idempotency_key}
            ),
            idempotency_key=idempotency_key,
        )

    async def refund(
        self, payment_id: str, amount_paise: int, idempotency_key: str
    ) -> dict[str, Any]:
        self._simulator.maybe_raise("refunds")
        return await self._call(
            # Payment.refund's signature is (payment_id, data={}, **kwargs) — unlike capture(),
            # there is no separate `amount` positional; it belongs inside `data`.
            lambda: self._client.payment.refund(
                payment_id,
                {"amount": amount_paise},
                headers={"X-Razorpay-Idempotency": idempotency_key},
            ),
            idempotency_key=idempotency_key,
        )

    async def _call(self, fn: Any, *, idempotency_key: str) -> dict[str, Any]:
        try:
            result: dict[str, Any] = await asyncio.to_thread(fn)
            return result
        except razorpay.errors.BadRequestError as exc:
            logger.warning("razorpay.4xx", error=str(exc), idempotency_key=idempotency_key)
            raise RazorpayAPIError(400, str(exc)) from exc
        except razorpay.errors.ServerError as exc:
            logger.warning("razorpay.5xx", error=str(exc), idempotency_key=idempotency_key)
            raise httpx.HTTPStatusError(
                str(exc), request=httpx.Request("POST", "https://api.razorpay.com"), response=httpx.Response(502)
            ) from exc


_counter = itertools.count()
