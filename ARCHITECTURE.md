# Architecture

Five layers, top to bottom. Every money-moving request flows straight down through all five and
back up; nothing skips a layer.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 1 — Surfaces                                                       │
│   Chat widget (React)   Merchant console (React)   MCP server            │
│        │                       │                        │               │
│        └───────────────┬───────┴───────────┬────────────┘               │
│                     POST /chat         WS /ws/merchant/*   MCP tools     │
└────────────────────────┼─────────────────────────────────┼──────────────┘
                          ▼                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 2 — LangGraph orchestrator (app/agents/)                           │
│                                                                           │
│              ┌──────────┐                                                │
│   message → │  router  │ → current_intent                                │
│              └────┬─────┘                                                │
│        ┌───────────┼────────────┬────────────┬───────────┐              │
│        ▼           ▼            ▼            ▼           ▼              │
│   discovery   cart_manager  recommender   checkout     support           │
│   (read-only)  (pure math)  (read-only)  (MONEY NODE)  (terminal)        │
└────────┼─────────────────────────────────────┼───────────────────────────┘
         │ read prices/stock                    │ ProposedAction
         ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 3 — Guardrails, the money firewall (app/guardrails/gate.py)        │
│                                                                           │
│   intent logged → idempotency key → policy_engine.evaluate()             │
│                                            │                             │
│                              ┌─────────────┼─────────────┐               │
│                            allow          gate           deny            │
│                              │              │              │             │
│                              │      confirmation.py        │             │
│                              │   (pause, notify merchant,   │            │
│                              │    await approve/deny)       │             │
│                              │              │              │             │
│                              └──────┬───────┘              │             │
│                                     ▼                       │            │
│                          idempotency.with_retry()            │           │
│                            (razorpay_client call)             │          │
│                                     │                          ▼         │
│                              explainer.build_trace()   graceful denial   │
└─────────────────────────────────────┼────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 4 — Integrations & data (app/integrations/, app/db/)               │
│                                                                           │
│   razorpay_client.py     vector_store.py (Qdrant)     session_store.py   │
│   (test-mode only,       (product embeddings,          (Redis: cart,    │
│    idempotent writes)     semantic search)              memory, spend)  │
│                                                                           │
│                    Postgres (products, merchant_configs,                 │
│                               pending_approvals)                         │
└─────────────────────────────────────┼────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 5 — Audit log (app/audit/) — append-only Postgres                  │
│                                                                           │
│   Every layer above writes here, keyed by trace_id.                      │
│   Money events (razorpay/checkout/guardrail/human) persist synchronously │
│   before the caller proceeds. Everything else drains from a bounded      │
│   async queue in the background. Nothing is ever updated or deleted.     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Why it's shaped this way

**One brain, two faces.** The chat widget and the MCP server are both thin surfaces over the same
LangGraph graph, the same guardrails gate, and the same audit log. An external AI buyer connecting
via MCP and a human typing in the chat widget produce indistinguishable audit trails — same policy
checks, same idempotency keys, same reason traces. See `app/mcp_server/tools.py`: every tool calls
into the exact service the chat endpoint uses, never a parallel implementation.

**Only one file can spend money.** `app/agents/checkout.py` is the only module in `app/agents/` that
imports `app/integrations/razorpay_client.py`, and even it never calls the client directly — every
write goes through `guardrails/gate.py::gate_money_action()`, the single public entry point the
guardrails package exposes. This isn't a convention enforced by code review; it's checked by an
AST-walking test (`tests/test_guardrails_invariants.py`) that fails the build if a new file imports
the Razorpay client from outside `checkout.py`.

**The guardrail is the LLM's boss.** `policy_engine.evaluate()` is pure Python — no LLM call, no
network I/O — and returns one of three verdicts: `allow`, `gate`, or `deny`. A `gate` verdict pauses
the flow in `confirmation.py`, which persists a `PendingApproval` row and blocks on an in-memory
`asyncio.Event` until a merchant resolves it via the console (pushed in real time over
`WS /ws/merchant/{id}`, backed by Redis pub/sub) or the request times out. There is no tool, prompt,
or code path that lets the LLM skip this wait.

**Every money action is bracketed by audit writes.** `gate_money_action()` writes a
`guardrail.intent_declared` event with the proposed action and a `guardrail.policy.<verdict>` event
with the reason *before* touching Razorpay, and a `razorpay.<action>.succeeded` (or
`.retry_exhausted`) event *after*. Both share one `trace_id`. If the pre-call write fails, the money
action never happens — see `app/audit/logger.py`'s synchronous path for `razorpay`/`checkout`/
`guardrail`/`human` actors.

**Determinism stays deterministic.** Cart totals (`cart_manager.compute_total`), policy checks
(`policy_engine.evaluate`), idempotency keys (`idempotency.key_for`), and reason traces
(`explainer.build_trace`) are pure functions with no LLM involvement — same inputs, same outputs,
every time. LLMs translate natural language into structured operations (which SKU, which intent)
and write the prose the user reads; they never compute a price or decide whether an action is
allowed.

## Data flow for one checkout

1. Shopper sends "checkout" → `POST /chat` → `router_node` classifies intent as `checkout`.
2. `checkout_node` computes the cart total (pure), builds a `ProposedAction`, and calls
   `gate_money_action()` with a closure that would call `razorpay_client.create_order`.
3. The gate logs intent, derives an idempotency key from `(session_id, cart_hash, action_type)`,
   evaluates policy against the merchant's `MerchantConfig` and the session's spend so far.
4. **Allow:** the gate invokes the closure (wrapped in `with_retry`), logs the result, returns.
   **Gate:** a `PendingApproval` row is written and the merchant console is notified over
   WebSocket; the gate awaits resolution. **Deny:** no Razorpay call is made at all.
5. On a Razorpay 5xx or timeout, `with_retry` retries up to 3 times with exponential backoff using
   the *same* idempotency key, then raises. `checkout_node` catches that and retries the whole
   gated flow against `create_payment_link` instead — same policy check, same audit trail, a
   different action type.
6. Every step above wrote to the audit log under one `trace_id`. `GET /audit/{trace_id}` (or the
   Audit Viewer) replays the full story — including the timeout and the fallback, if it happened.
