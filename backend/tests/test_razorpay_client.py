"""Invariant #5: test mode is enforced in code. A live-looking key must never construct a client.
Also regression-tests the wrapper's calls against the REAL razorpay-python `Client.request` method
signature (not a mock of RazorpayClient itself) — a mismatched positional/keyword arg here would
raise a TypeError at call time that a higher-level mock would never catch.
"""

import json

import pytest

from app.config import Settings
from app.integrations.razorpay_client import RazorpayClient, RazorpayTestModeError


def test_live_key_raises_on_init() -> None:
    live_settings = Settings(RAZORPAY_KEY_ID="rzp_live_abc123", RAZORPAY_KEY_SECRET="s")
    with pytest.raises(RazorpayTestModeError):
        RazorpayClient(settings=live_settings)


def test_test_key_initializes_cleanly() -> None:
    test_settings = Settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="s")
    client = RazorpayClient(settings=test_settings)
    assert client is not None


def test_garbage_key_raises_on_init() -> None:
    bad_settings = Settings(RAZORPAY_KEY_ID="not-a-razorpay-key", RAZORPAY_KEY_SECRET="s")
    with pytest.raises(RazorpayTestModeError):
        RazorpayClient(settings=bad_settings)


class _CapturedCall:
    def __init__(self, method: str, path: str, options: dict) -> None:
        self.method = method
        self.path = path
        self.options = options


@pytest.fixture
def captured_requests(monkeypatch):  # type: ignore[no-untyped-def]
    """Patches the real razorpay.Client.request (not RazorpayClient) so these tests exercise the
    actual SDK call shape our wrapper produces, and returns a fake JSON body instead of hitting the
    network."""
    import razorpay

    calls: list[_CapturedCall] = []

    def fake_request(self, method: str, path: str, **options):  # type: ignore[no-untyped-def]
        calls.append(_CapturedCall(method, path, options))
        return {"id": "fake_id_123", "status": "ok"}

    monkeypatch.setattr(razorpay.Client, "request", fake_request)
    return calls


def _client() -> RazorpayClient:
    return RazorpayClient(settings=Settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="s"))


@pytest.mark.asyncio
async def test_create_order_sends_idempotency_header(captured_requests) -> None:  # type: ignore[no-untyped-def]
    result = await _client().create_order(100_000, "INR", "receipt-1", {}, idempotency_key="key-1")
    assert result["id"] == "fake_id_123"
    assert captured_requests[0].options["headers"]["X-Razorpay-Idempotency"] == "key-1"


@pytest.mark.asyncio
async def test_capture_payment_matches_sdk_signature(captured_requests) -> None:  # type: ignore[no-untyped-def]
    result = await _client().capture_payment("pay_123", 100_000, idempotency_key="key-2")
    assert result["id"] == "fake_id_123"
    call = captured_requests[0]
    assert call.options["headers"]["X-Razorpay-Idempotency"] == "key-2"
    assert json.loads(call.options["data"])["amount"] == 100_000


@pytest.mark.asyncio
async def test_refund_matches_sdk_signature(captured_requests) -> None:  # type: ignore[no-untyped-def]
    """Payment.refund's real signature is (payment_id, data={}, **kwargs) — no separate `amount`
    positional. The amount must be inside `data`."""
    result = await _client().refund("pay_123", 50_000, idempotency_key="key-3")
    assert result["id"] == "fake_id_123"
    call = captured_requests[0]
    assert json.loads(call.options["data"])["amount"] == 50_000
    assert call.options["headers"]["X-Razorpay-Idempotency"] == "key-3"


@pytest.mark.asyncio
async def test_create_payment_link_matches_sdk_signature(captured_requests) -> None:  # type: ignore[no-untyped-def]
    result = await _client().create_payment_link(
        100_000, "test order", {"email": "buyer@example.com"}, idempotency_key="key-4"
    )
    assert result["id"] == "fake_id_123"
    call = captured_requests[0]
    assert call.options["headers"]["X-Razorpay-Idempotency"] == "key-4"
    assert json.loads(call.options["data"])["amount"] == 100_000
