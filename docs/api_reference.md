# API reference

Base URL: `http://localhost:8000` (local dev). Every response includes an `x-trace-id` header, and
every JSON body that represents a traced action includes a `trace_id` field retrievable via
`GET /audit/{trace_id}`.

## Chat

### `POST /chat`

Send a message and get the agent's response. If `Accept: text/event-stream` is set, streams the
response as SSE instead of returning a single JSON body.

**Request**

```json
{ "session_id": "optional-existing-session-id", "message": "show me wireless keyboards" }
```

**Response**

```json
{
  "session_id": "a1b2c3d4-...",
  "trace_id": "e5f6a7b8-...",
  "response": "I found a couple of great wireless keyboards for you...",
  "cart": { "items": [] },
  "pending_approval_id": null,
  "reason_trace": null,
  "discovery_results": [
    { "sku": "ELEC-001", "name": "Wireless Mechanical Keyboard", "price_paise": 349900, "category": "electronics", "stock": 42 }
  ],
  "recommendations": []
}
```

- `pending_approval_id` is set when a checkout was gated and didn't resolve within the endpoint's
  short race window (~3s) — the graph keeps running in the background; poll
  `GET /merchant/{merchant_id}/approvals` or watch the merchant console WebSocket for resolution.
- `reason_trace` is populated on checkout turns (see the `ReasonTrace` shape below); `null`
  otherwise.

**SSE variant** (`Accept: text/event-stream`): emits `event: start` (session/trace ids),
`data: {"token": "..."}` chunks, then `event: done` with the final cart/reason_trace/results, or
`event: pending_approval` if the gate didn't resolve in time.

## Audit

### `GET /audit/{trace_id}?limit=100&offset=0`

Returns every audit event for a trace, ordered by timestamp ascending. Payload fields named
`card_number`, `cvv`, `token`, or `secret` (recursively, at any nesting level) are redacted to
`"***"` before the response is built — this endpoint never returns sensitive data even if it were
somehow logged.

```json
[
  {
    "id": "...", "trace_id": "...", "session_id": "...", "timestamp": "2026-09-03T12:00:00Z",
    "actor": "guardrail", "event_type": "guardrail.policy.allow",
    "payload": { "matched_rules": ["within_policy"], "amount_paise": 349900 },
    "reason": "Within all merchant policy limits."
  }
]
```

`actor` is one of `router`, `discovery`, `recommender`, `cart_manager`, `support`, `checkout`,
`guardrail`, `razorpay`, `human`.

## Merchant console

All `/merchant/*` endpoints require an `X-Merchant-Key` header matching `MERCHANT_API_KEY`.

| Method & path | Purpose |
|---|---|
| `GET /merchant/{merchant_id}/config` | Fetch policy (creates a default row on first access). |
| `PUT /merchant/{merchant_id}/config` | Partial update — any subset of `per_session_cap_paise`, `per_transaction_cap_paise`, `allowed_payment_methods`, `blocked_categories`, `auto_approve_threshold_paise`. |
| `GET /merchant/{merchant_id}/approvals?status=pending` | List approvals, optionally filtered by status (`pending`/`approved`/`denied`/`timed_out`). |
| `POST /merchant/approvals/{approval_id}/approve` | Resolve a pending approval as approved. `404` if nothing is currently waiting on that id. |
| `POST /merchant/approvals/{approval_id}/deny` | Resolve a pending approval as denied. |

### `WS /ws/merchant/{merchant_id}`

Streams `{"type": "approval.created", ...}` and `{"type": "approval.resolved", ...}` events in real
time, backed by Redis pub/sub — the same channel the confirmation gate publishes to. No polling.

## MCP server

`python -m app.mcp_server.server` (stdio) or `--http` (streamable-http). Every tool below calls into
the exact same services `/chat` uses — see `app/mcp_server/tools.py`.

| Tool | Args | Returns |
|---|---|---|
| `search_catalog` | `query, category?, limit=5` | Validated product list (sku, name, description, price_paise, category, stock) |
| `get_product` | `sku` | One product or `null` |
| `create_cart` | — | `cart_id` |
| `add_to_cart` | `cart_id, sku, qty=1` | `{cart, total_paise}` — raises if the SKU is unknown or out of stock |
| `remove_from_cart` | `cart_id, sku` | `{cart, total_paise}` |
| `checkout_cart` | `cart_id, customer_email` | `{message, cart, guardrail_decision, trace_id}` — may take up to 5 minutes if gated for merchant approval |

## Reason trace shape

Attached to checkout turns via `reason_trace` and to every `guardrail_decision` a node returns:

```json
{
  "summary": "Auto-approved: Within all merchant policy limits.",
  "rules_matched": ["within_policy"],
  "session_spend_before": 0,
  "session_spend_after": 349900,
  "approval_source": "auto",
  "idempotency_key": "6f1b1a3e-..."
}
```

`approval_source` is `"auto"` (allowed, no human needed), `"human"` (gated, a merchant approved
it), or `"denied"` (policy denial, or gated-and-denied/timed-out).
