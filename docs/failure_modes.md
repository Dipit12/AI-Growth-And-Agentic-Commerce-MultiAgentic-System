# Failure modes

Three reproducible scenarios, each with a demo script and a dedicated test. The audit trail is the
point: every one of these produces a complete, replayable story — a failure is not a gap in the log.

## 1. Payment timeout → automatic fallback to a payment link

**Script:** `python -m scripts.demo_timeout`
**Test:** `tests/test_failure_modes.py::test_three_timeouts_fall_back_to_payment_link_with_no_double_charge`

`app/integrations/razorpay_client.py` has a test-only seam: setting `SIMULATE_RAZORPAY_TIMEOUT=
"orders:3"` makes the next 3 calls to `create_order` raise `httpx.TimeoutException` before touching
the network. Combined with `guardrails/idempotency.py::with_retry(max_attempts=3)`, this reproduces
a realistic "Razorpay is having a bad minute" scenario deterministically:

1. `checkout_node` proposes `create_order` via `gate_money_action`.
2. Attempt 1 → timeout. Attempt 2 → timeout. Attempt 3 → timeout. `with_retry` logs a
   `razorpay.retry_exhausted` audit event and raises `RetryExhaustedError`.
3. `checkout_node` catches that and retries the *entire gated flow* against
   `create_payment_link` instead — same policy check, same audit trail, a new idempotency key
   derived from `action_type="create_payment_link"` (the key formula includes action type, so the
   fallback never collides with the original attempt's key).
4. The fallback succeeds; the user gets a payment link instead of an inline confirmation, and the
   cart is cleared.

**What the test asserts that a shallower test wouldn't:** exactly one `*.succeeded` audit event
exists for the whole checkout — either the order or the payment link, never both. A real double
charge would show up as two successful money events under one trace_id, which is exactly what this
test would catch.

## 2. Policy denial — handled gracefully, cart preserved

**Script:** `python -m scripts.demo_denial`
**Test:** `tests/test_failure_modes.py::test_over_cap_checkout_is_denied_with_no_razorpay_call`

The script sets a scratch merchant's `per_transaction_cap_paise` to ₹500 and attempts a ₹5000
checkout. `policy_engine.evaluate()` denies it on the `transaction_cap_exceeded` rule *before*
`gate_money_action` ever constructs a Razorpay call — the razorpay_client passed into this test is a
stub whose `create_order` raises `AssertionError` if invoked at all, so any regression that
accidentally reaches the network fails loudly.

The user sees: *"This exceeds the per-transaction limit set by the merchant. You can reduce the
cart or contact the merchant to increase your limit."* The cart is untouched — `checkout_node`
returns no `cart` key in its partial state update on a denial, so LangGraph's state merge leaves the
existing cart exactly as it was. The audit trail still records the full reasoning:
`guardrail.intent_declared` → `guardrail.policy.deny` (reason: transaction cap exceeded) — nothing
silently disappears just because the answer was "no."

## 3. Discovery hallucination — caught before it reaches the user

**Script:** `python -m scripts.demo_hallucination`
**Test:** `tests/test_failure_modes.py::test_fake_sku_from_discovery_llm_never_reaches_the_user`

`discovery_node` asks the LLM to choose which catalog-search-returned SKUs to present, as JSON. The
demo forces this LLM call (via the same `llm_call` injection seam every node accepts) to return a
SKU — `FAKE-SKU-999` — that was never in the search results and doesn't exist in Postgres:

```json
{"skus": ["ELEC-001", "FAKE-SKU-999"], "message": "Found a great keyboard, plus a bonus deal!"}
```

Every SKU the LLM names is independently re-validated against Postgres (`SELECT ... WHERE sku = ...
AND stock > 0`) before being added to `discovery_results`. `FAKE-SKU-999` fails that check, gets
dropped, and an `agent.discovery.hallucination_caught` audit event is written with the removed SKU
list and the original query. `ELEC-001` — the real product — still reaches the user normally. The
assertion that matters: `"FAKE-SKU-999" not in shown_skus`, checked against what the node actually
returns to the caller, not just against what got logged.

## Replaying any of these

Every script prints its `trace_id`. Replay the full timeline with:

```bash
python -m scripts.replay_audit <trace_id> --verbose
```

or paste the `trace_id` into the Audit Viewer (`/audit/:traceId` in the frontend) for the color-coded,
expandable version — this is what the demo video shows during each failure scenario.
