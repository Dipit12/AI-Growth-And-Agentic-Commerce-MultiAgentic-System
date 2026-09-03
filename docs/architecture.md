# Architecture — deep dive

See `../ARCHITECTURE.md` for the layered diagram and the high-level "why." This document goes
module by module.

## Layer 1 — Surfaces

| Surface | Entry point | Notes |
|---|---|---|
| Chat widget | `frontend/src/routes/Chat.tsx` | Talks to `POST /chat`. Session id persisted in `localStorage`. |
| Merchant console | `frontend/src/routes/Merchant.tsx` | Policy form + approval queue. Live updates via `WS /ws/merchant/{merchant_id}`. |
| Audit viewer | `frontend/src/routes/Audit.tsx` | `GET /audit/{trace_id}`, color-coded timeline, JSON export. |
| MCP server | `backend/app/mcp_server/server.py` | `stdio` for local MCP clients (e.g. Claude Desktop), `streamable-http` for the demo. |

`POST /chat` (`backend/app/api/chat.py`) is the one HTTP entry point every conversational turn goes
through. It loads the session's cart and message history from Redis, appends the new user message,
invokes the compiled LangGraph (`app/agents/graph.py::chat_graph`), and persists the result. Because
a gated checkout can legitimately wait up to five minutes for merchant approval
(`guardrails/confirmation.py`'s default `timeout_seconds=300`), the endpoint races the graph
invocation against a short (`GATE_RACE_TIMEOUT_SECONDS = 3.0`) timeout: if the graph hasn't finished
by then, the request returns immediately with a `pending_approval_id` (looked up from the
`PendingApproval` row the confirmation gate already wrote synchronously) while the graph keeps
running to completion in a background `asyncio.Task`. The merchant console's WebSocket and a
follow-up chat turn are both ways to observe the eventual outcome.

## Layer 2 — LangGraph orchestrator (`app/agents/`)

`app/agents/state.py` defines `AgentState` as a `TypedDict` (LangGraph's native shape) — Pydantic
models are reserved for anything crossing into the guardrails/audit layers. `app/agents/graph.py`
wires a `StateGraph` with `router` as the entry point and a conditional edge to one of five nodes
based on `current_intent`. Each of those five nodes is terminal *within one graph invocation*: one
HTTP turn is one `ainvoke` call over the full message history, and "the next turn starts back at the
router" happens naturally because the next `POST /chat` re-enters at `router` with the updated
history — not via an in-graph back-edge, which would have no new input to react to.

| Node | File | Can touch money? | Can touch DB writes? |
|---|---|---|---|
| `router` | `router.py` | No | No (only reads message history) |
| `discovery` | `discovery.py` | No | Reads `products` for validation |
| `cart_manager` | `cart_manager.py` | No | Reads `products` to price additions |
| `recommender` | `recommender.py` | No | No |
| `checkout` | `checkout.py` | **Yes — the only one** | Via `gate_money_action` only |
| `support` | `support.py` | No | No |

Every node accepts its collaborators (LLM call, vector store, session factory, session store,
audit logger) as optional keyword arguments defaulting to the real implementation — this is what
lets the test suite inject fakes without touching the network, and it's the same seam
`scripts/demo_hallucination.py` uses to force a reproducible failure.

**Discovery's hallucination guard.** The discovery LLM is asked to pick which SKUs (from a set
already returned by `VectorStore.search`) to show the user, as JSON. Every SKU it names is then
re-validated against Postgres (exists, in stock) before it reaches `discovery_results`. Anything
that fails validation is dropped and logged as `agent.discovery.hallucination_caught` — this is
deliberately robust to a fabricated SKU that was never in the search results at all, not just a
stale one.

**Cart math.** `cart_manager.add_item` / `remove_item` / `compute_total` are pure functions over a
plain `Cart` TypedDict — integer paise in, integer paise out, no floats anywhere. The LLM only
chooses *which* structured operation (`add`/`remove`/`none`) and *which* SKU; `cart_manager_node`
does the actual mutation and Redis persistence.

## Layer 3 — Guardrails (`app/guardrails/`)

This is the differentiator layer, and it has one public function:
`gate.py::gate_money_action(action, trace_id, session_id, razorpay_call, *, audit, session_factory,
session_store, merchant_id)`.

- **`policy_engine.py`** — `evaluate(action, merchant_config, session_spend_so_far)`, pure. Rule
  order is fixed and tested individually in `tests/test_policy_engine.py`: blocked category →
  disallowed payment method → session cap → per-transaction cap → auto-approve threshold → allow.
- **`idempotency.py`** — `key_for(session_id, cart_hash, action_type)` is a UUIDv5 hash, so a retry
  after a network timeout reproduces the exact same key Razorpay sees. `with_retry()` retries only
  `httpx.TimeoutException`, `httpx.NetworkError`, and 5xx `httpx.HTTPStatusError` — never 4xx.
- **`confirmation.py`** — `request_approval()` persists a `PendingApproval` row, publishes a
  `merchant:{merchant_id}:approvals` Redis pub/sub event, and awaits an in-process
  `asyncio.Event` keyed by approval id. `resolve_approval()` (called from the merchant console API)
  sets that event. The wait uses an in-memory registry rather than DB polling, per plan.md's Step 19.
- **`explainer.py`** — `build_trace()` assembles the `ReasonTrace` attached to every money event:
  summary, matched rules, spend before/after, approval source (`auto`/`human`/`denied`), idempotency
  key. Deterministic — same inputs, same trace, every time.

## Layer 4 — Integrations (`app/integrations/`)

- **`razorpay_client.py`** asserts `RAZORPAY_KEY_ID.startswith("rzp_test_")` at construction and
  refuses to initialize otherwise — this is a code-level guard, not just a config convention. Every
  write method takes an explicit `idempotency_key` and forwards it as the `X-Razorpay-Idempotency`
  header. A `SIMULATE_RAZORPAY_TIMEOUT="orders:3"` settings override makes the next N calls to a
  named endpoint raise `httpx.TimeoutException` before touching the network — the seam
  `scripts/demo_timeout.py` uses for a reproducible retry-then-fallback run.
- **`vector_store.py`** wraps Qdrant. Embeddings come from Voyage AI's `voyage-3` if
  `VOYAGE_API_KEY` is set, otherwise a local Ollama `nomic-embed-text` model — see the module
  docstring for the tradeoff.
- **`session_store.py`** wraps `redis.asyncio`. All keys are namespaced `session:{session_id}:...`
  with a 24h TTL; cart, message history, and a running spend counter live here.

## Layer 5 — Audit (`app/audit/`)

`AuditEvent` is append-only by construction — the model exposes no update/delete helper.
`AuditLogger` (`logger.py`) routes writes two ways: anything from the `razorpay`, `checkout`,
`guardrail`, or `human` actors (i.e., anything tied to a money decision) is persisted synchronously,
blocking the caller until the write lands — this is what makes "pre-call record captures intent,
post-call record captures the response, both share a trace_id" an actual guarantee rather than a
hope. Everything else (router/discovery/recommender/cart_manager chatter) goes through a bounded
`asyncio.Queue` drained by a background task; on overflow those are dropped with a `structlog.error`
rather than ever blocking a chat turn.

## Testing strategy without live infrastructure

This repo's dev sandbox has no Docker, no local Postgres/Redis/Qdrant, and no real Razorpay/
Anthropic credentials. Rather than skip API-level testing, `backend/tests/conftest.py` points
`POSTGRES_URL` at a file-backed SQLite database for the whole test session (SQLite's `:memory:`
doesn't survive across pooled connections; a file does) — the *same* engine `app/db/session.py`
creates at import time, so the real FastAPI app, real routers, and real SQLAlchemy models are all
exercised as-is. Redis-backed singletons (`app/api/deps.py::session_store`) are monkeypatched to
`fakeredis` per test. The result: `tests/test_chat_api.py`, `tests/test_merchant_api.py`, and
`tests/test_audit_api.py` drive the actual `app.main:app` over `httpx.AsyncClient` — including a
real end-to-end approve/deny flow through the in-process `ApprovalRegistry` — without needing
`docker compose up`. A real deployment still needs real Postgres/Redis/Qdrant; this only removes
that requirement from the test suite.
