# plan.md

Step-by-step implementation guide for Claude Code. Each step is a self-contained task with a clear goal, files to touch, key decisions, and a verification check. Work through steps in order — later steps assume earlier ones are complete.

## How to use this document

- Give Claude Code one step at a time. Do not paste multiple steps at once — the guardrails logic in particular needs focused attention.
- After each step, run the verification check under **Done when**. If it fails, iterate before moving on.
- Commit after every step with a message like `step N — <title>`. Small commits make rollbacks easy.
- Every step assumes `CLAUDE.md` has been read and its invariants are respected.
- If Claude Code proposes shortcuts that bypass guardrails, deviate from the file structure, or call Razorpay APIs from anywhere except `agents/checkout.py`, reject the change and re-prompt.

## Global conventions (apply to every step)

- Python 3.11+, async everywhere, `mypy --strict` on `app/guardrails/` and `app/agents/checkout.py`.
- Pydantic v2 for all cross-module payloads.
- `structlog` for logging with `trace_id`, `session_id`, `node_name` in every event.
- Environment variables via `pydantic-settings`; never hardcode secrets.
- Every new file gets a one-line module docstring explaining its role and which architecture layer it belongs to.

---

## Phase 0 — Repo setup

### Step 1 — Initialize repository structure

**Goal:** Create the directory skeleton and root config files.

**Files:** `README.md`, `.gitignore`, `.env.example`, `LICENSE` (MIT), `docker-compose.yml`, empty `backend/`, `frontend/`, `docs/` folders with `.gitkeep`.

**Details:**
- `.gitignore`: standard Python + Node + `.env` + `__pycache__` + `node_modules` + `.venv` + `dist`.
- `.env.example` includes placeholders for `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `ANTHROPIC_API_KEY`, `POSTGRES_URL`, `REDIS_URL`, `QDRANT_URL`.
- `README.md` for now is just the project name and "See `plan.md` for build steps."

**Done when:** `git status` shows a clean working tree after initial commit.

### Step 2 — Docker Compose for local dev

**Goal:** Bring up Postgres 16, Redis 7, and Qdrant with one command.

**Files:** `docker-compose.yml`.

**Details:**
- Postgres: expose 5432, set a dev password, mount a named volume for persistence.
- Redis: expose 6379.
- Qdrant: expose 6333 (HTTP) and 6334 (gRPC), mount a named volume for storage.
- Add a healthcheck to each service.

**Done when:** `docker compose up -d && docker compose ps` shows all three services healthy.

### Step 3 — Backend scaffold

**Goal:** Bootstrap the FastAPI backend with async structure.

**Files:** `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/config.py`.

**Details:**
- Dependencies: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `structlog`, `asyncpg`, `sqlalchemy[asyncio]`, `alembic`, `redis`, `qdrant-client`, `razorpay`, `langgraph`, `langchain-anthropic`, `anthropic`, `httpx`, `pytest`, `pytest-asyncio`, `mypy`.
- `config.py`: Pydantic `Settings` class loading from `.env`, with typed fields for every env var.
- `main.py`: create FastAPI app, mount a `/health` route returning `{"status": "ok", "trace_id": <uuid>}`.

**Done when:** `uvicorn app.main:app --reload` starts and `curl localhost:8000/health` returns 200.

### Step 4 — Structured logging

**Goal:** Every log line carries `trace_id`, `session_id`, `node_name`.

**Files:** `backend/app/logging_config.py`, edits to `backend/app/main.py`.

**Details:**
- Configure `structlog` with JSON output in production, pretty output in dev (`ENV=dev`).
- Add a FastAPI middleware that generates a `trace_id` per request and binds it to the structlog context.
- Replace any `print` statements with structlog calls.

**Done when:** hitting `/health` logs a JSON line containing `trace_id`.

### Step 5 — Test infrastructure

**Goal:** `pytest` runs with async support and a working fixture pattern.

**Files:** `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/pyproject.toml` (pytest config).

**Details:**
- `pytest-asyncio` in strict mode.
- Fixtures for an in-memory app client, a mocked Redis, a mocked Postgres session.
- One smoke test hitting `/health`.

**Done when:** `pytest` runs green with at least one passing test.

---

## Phase 1 — Data foundation

### Step 6 — Alembic + Postgres session

**Goal:** Async SQLAlchemy engine + Alembic migrations wired up.

**Files:** `backend/app/db/__init__.py`, `backend/app/db/session.py`, `backend/app/db/base.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`.

**Details:**
- `session.py`: async engine, `async_sessionmaker`, `get_session` dependency.
- `base.py`: declarative base class all models will inherit from.
- Alembic configured for async migrations against the Postgres URL from settings.

**Done when:** `alembic revision --autogenerate -m "init"` produces an empty migration file without errors.

### Step 7 — Product and catalog models

**Goal:** Persistent product catalog.

**Files:** `backend/app/models/product.py`, new Alembic migration.

**Details:**
- SQLAlchemy model `Product`: `id` (UUID), `sku` (unique), `name`, `description`, `price_paise` (int — store money as integer paise, never float), `stock`, `category`, `attributes` (JSONB), `embedding_id` (nullable UUID pointing at Qdrant), `created_at`, `updated_at`.
- Pydantic schemas `ProductCreate`, `ProductRead` in the same file or a sibling `schemas/product.py`.

**Done when:** `alembic upgrade head` creates the `products` table and `psql \d products` shows expected columns.

### Step 8 — Audit event model

**Goal:** Append-only audit log table.

**Files:** `backend/app/audit/models.py`, new Alembic migration.

**Details:**
- `AuditEvent` model: `id` (UUID), `trace_id` (UUID, indexed), `session_id` (UUID, indexed), `timestamp` (default now), `actor` (enum: `router`, `discovery`, `recommender`, `cart_manager`, `checkout`, `guardrail`, `razorpay`, `human`), `event_type` (string), `payload` (JSONB), `reason` (text nullable).
- Do NOT add an update or delete method to the model — appends only.
- Add a composite index on `(trace_id, timestamp)`.

**Done when:** migration applies cleanly; a manual `INSERT` into `audit_events` works and `SELECT` by `trace_id` uses the index.

### Step 9 — Merchant config and pending approvals

**Goal:** Persistent merchant policy and the queue for human-in-the-loop approvals.

**Files:** `backend/app/models/merchant_config.py`, `backend/app/models/pending_approval.py`, new Alembic migration.

**Details:**
- `MerchantConfig`: `merchant_id` (PK), `per_session_cap_paise`, `per_transaction_cap_paise`, `allowed_payment_methods` (JSON list), `blocked_categories` (JSON list), `auto_approve_threshold_paise`, `updated_at`. Seed one default row for `merchant_id="demo"`.
- `PendingApproval`: `id`, `trace_id`, `session_id`, `action_type`, `payload` (JSONB), `status` (enum: `pending`, `approved`, `denied`, `timed_out`), `created_at`, `resolved_at`, `resolved_by`.

**Done when:** migration applies, and a seed script or fixture inserts the default `demo` merchant.

### Step 10 — Seed catalog script

**Goal:** Populate ~50 realistic synthetic products.

**Files:** `backend/scripts/seed_catalog.py`, `backend/scripts/__init__.py`.

**Details:**
- 50 products across 5 categories (electronics, kitchen, stationery, fitness, home). Realistic Indian-market prices in paise.
- Each product gets a descriptive `description` (~2 sentences) — this is what will be embedded later.
- Idempotent: safe to run twice. Use `sku` uniqueness to skip existing rows.
- Do NOT generate embeddings here — that's Step 12.

**Done when:** `python -m scripts.seed_catalog` runs and `SELECT count(*) FROM products` returns 50.

---

## Phase 2 — Integration layer (Layer 4)

### Step 11 — Razorpay client wrapper

**Goal:** Async wrapper around Razorpay SDK with a hard test-mode guard.

**Files:** `backend/app/integrations/__init__.py`, `backend/app/integrations/razorpay_client.py`, `backend/tests/test_razorpay_client.py`.

**Details:**
- Class `RazorpayClient` initialized from settings. In `__init__`, assert `settings.RAZORPAY_KEY_ID.startswith("rzp_test_")`. Raise on failure. This assertion must never be removed.
- Async methods: `create_order(amount_paise, currency, receipt, notes, idempotency_key)`, `capture_payment(payment_id, amount_paise, idempotency_key)`, `create_payment_link(amount_paise, description, customer, idempotency_key)`, `refund(payment_id, amount_paise, idempotency_key)`.
- The Razorpay SDK is sync — wrap calls in `asyncio.to_thread`.
- Every method takes an explicit `idempotency_key` argument and passes it to Razorpay's `X-Razorpay-Idempotency` header (via the `headers` param on the SDK).
- Test: instantiate with a `rzp_live_` key and assert it raises.

**Done when:** the test-mode-guard test passes, and a manual smoke test can create a test-mode order.

### Step 12 — Vector store + embedding indexer

**Goal:** Qdrant client and a script that embeds all seeded products.

**Files:** `backend/app/integrations/vector_store.py`, `backend/scripts/index_catalog.py`.

**Details:**
- `VectorStore` class: `create_collection`, `upsert(product_id, text, metadata)`, `search(query, filters, k)`.
- Use Anthropic embeddings via the `voyage-3` model through the Anthropic SDK, or Ollama's `nomic-embed-text` locally if that's already set up. Pick one and document the choice in the module docstring.
- `index_catalog.py`: loads all products from Postgres, embeds `"{name}. {description}. Category: {category}."`, upserts into Qdrant, writes back the `embedding_id`.
- Idempotent: re-running does not duplicate.

**Done when:** `python -m scripts.index_catalog` runs, and `VectorStore.search("wireless keyboard")` returns relevant products.

### Step 13 — Redis session store

**Goal:** Async wrapper for cart, memory, and policy counters.

**Files:** `backend/app/integrations/session_store.py`, `backend/tests/test_session_store.py`.

**Details:**
- Use `redis.asyncio`.
- Methods: `get_cart(session_id)`, `set_cart(session_id, cart)`, `append_message(session_id, message)`, `get_messages(session_id, limit)`, `incr_session_spend(session_id, amount_paise)`, `get_session_spend(session_id)`.
- All keys prefixed by `session:{session_id}:`. Set TTL of 24 hours on session keys.
- Cart is stored as JSON.

**Done when:** unit tests using `fakeredis` pass for all methods.

---

## Phase 3 — Audit layer (Layer 5)

### Step 14 — Audit logger

**Goal:** Central writer for all audit events, with a synchronous path for money events.

**Files:** `backend/app/audit/logger.py`, `backend/app/audit/__init__.py`, `backend/tests/test_audit_logger.py`.

**Details:**
- Class `AuditLogger` with methods `log_agent_event`, `log_guardrail_event`, `log_razorpay_event`, `log_human_event`.
- Money events (any event where `actor in ("razorpay", "checkout")` OR `event_type` contains `"payment"`, `"order"`, `"refund"`) are written synchronously — the caller awaits confirmation of persistence before proceeding.
- Non-money events go through a bounded `asyncio.Queue` with a background task draining to Postgres. Queue overflow drops non-money events with a `structlog.error`, never blocks.
- Every event carries `trace_id`, and if a `trace_id` is missing, generate one and log a warning.

**Done when:** tests confirm money events block until persisted; non-money events do not.

### Step 15 — Audit query API

**Goal:** Endpoint to fetch all events for a `trace_id`, ordered.

**Files:** `backend/app/api/__init__.py`, `backend/app/api/audit.py`, edits to `main.py`.

**Details:**
- `GET /audit/{trace_id}` returns a JSON list of events in timestamp order.
- Add pagination via `limit`/`offset` query params, default `limit=100`.
- Include a redaction layer: never return raw payloads containing keys named `card_number`, `cvv`, `token`, `secret`. Replace values with `"***"`.

**Done when:** manually inserting three events with the same `trace_id` and calling the endpoint returns them in order.

### Step 16 — Replay script

**Goal:** CLI that pretty-prints a trace for debugging and demos.

**Files:** `backend/scripts/replay_audit.py`.

**Details:**
- `python -m scripts.replay_audit <trace_id>` fetches events and prints them with color coding by `actor` (use `rich`).
- Show relative timestamps (e.g., `+0.024s`) rather than absolute.
- If `--verbose`, print full payloads; otherwise, print a one-line summary per event.

**Done when:** running against a real `trace_id` produces a readable timeline.

---

## Phase 4 — Guardrails (Layer 3) — the money firewall

**These steps are the differentiator. Give them focused attention and complete tests before moving on.**

### Step 17 — Policy engine

**Goal:** Pure function that evaluates a proposed action against merchant policy.

**Files:** `backend/app/guardrails/__init__.py`, `backend/app/guardrails/policy_engine.py`, `backend/app/models/policy.py`, `backend/tests/test_policy_engine.py`.

**Details:**
- Pydantic models: `ProposedAction(action_type, amount_paise, category, payment_method, session_id, cart_hash)`, `PolicyDecision(verdict: Literal["allow", "gate", "deny"], reason: str, matched_rules: list[str])`.
- Function `evaluate(action, merchant_config, session_spend_so_far) -> PolicyDecision`. Pure — no I/O.
- Rules in order: deny if `category in blocked_categories`, deny if `payment_method not in allowed`, deny if `session_spend + amount > per_session_cap`, deny if `amount > per_transaction_cap`, gate if `amount > auto_approve_threshold`, else allow.
- Tests: one per rule, both passing and failing case.

**Done when:** the test suite covers every branch; `pytest tests/test_policy_engine.py -v` shows all rules named.

### Step 18 — Idempotency + retry

**Goal:** Deterministic key generation and exponential-backoff retry wrapper.

**Files:** `backend/app/guardrails/idempotency.py`, `backend/tests/test_idempotency.py`.

**Details:**
- Function `key_for(session_id, cart_hash, action_type) -> str` returning a stable UUIDv5 hash. Same inputs must always produce the same key.
- Async decorator `@with_retry(max_attempts=3, base_delay=0.5)` for wrapping Razorpay calls. Retry on `httpx.TimeoutException`, `httpx.NetworkError`, and Razorpay 5xx errors. Never retry on 4xx.
- On final failure, log a `razorpay_retry_exhausted` audit event and re-raise.

**Done when:** tests verify deterministic keys and that a 3-timeout-then-success scenario succeeds within 4 attempts.

### Step 19 — Confirmation gate

**Goal:** Pause the flow, persist a pending approval, wait for resolution.

**Files:** `backend/app/guardrails/confirmation.py`, `backend/app/api/merchant.py`, `backend/tests/test_confirmation.py`.

**Details:**
- Function `request_approval(action: ProposedAction, trace_id, timeout_seconds=300) -> ApprovalResult`. Inserts a `PendingApproval` row, sends a WebSocket notification to the merchant console, then polls (or awaits an event) until status changes or timeout.
- On timeout, mark the approval `timed_out` and return a denial result.
- `POST /merchant/approvals/{approval_id}/approve` and `.../deny` endpoints, protected by a simple merchant API key for the demo.
- The polling implementation should use Redis pub/sub or an `asyncio.Event` in-memory registry — not database polling.

**Done when:** an integration test creates a pending approval, resolves it via the API, and confirms the gate function returns the correct verdict.

### Step 20 — Explainer / reason trace

**Goal:** Assemble the human-readable rationale attached to every money event.

**Files:** `backend/app/guardrails/explainer.py`, `backend/tests/test_explainer.py`.

**Details:**
- Function `build_trace(action, policy_decision, approval_result, session_state) -> ReasonTrace`.
- `ReasonTrace` is a Pydantic model with fields: `summary` (one-sentence), `rules_matched`, `session_spend_before`, `session_spend_after`, `approval_source` (`"auto" | "human" | "denied"`), `idempotency_key`.
- Deterministic — same inputs, same output. No LLM.

**Done when:** tests verify the trace text for auto-approve, gated-approved, gated-denied, and denied-by-policy paths.

### Step 21 — Guardrails public API

**Goal:** One entry point that agents call; hides all four sub-modules.

**Files:** `backend/app/guardrails/gate.py`, edits to `backend/app/guardrails/__init__.py`.

**Details:**
- Async function `gate_money_action(action: ProposedAction, trace_id, session_id, razorpay_call: Callable) -> GateResult`.
- Flow: log intent → generate idempotency key → evaluate policy → if `gate`, request approval → if approved or auto-allowed, invoke `razorpay_call(idempotency_key)` with retry wrapper → log outcome + reason trace.
- `GateResult` includes the Razorpay response (or the denial reason), the reason trace, and the audit event IDs.
- This is the ONLY function `agents/checkout.py` will import from guardrails.

**Done when:** an integration test uses this function to run a full happy path against a mocked Razorpay client, and every expected audit event is written.

### Step 22 — Guardrails invariant test suite

**Goal:** One named test per invariant in `CLAUDE.md`.

**Files:** `backend/tests/test_guardrails_invariants.py`.

**Details:**
- Test for each invariant #1–#9 from `CLAUDE.md`. For invariants that are structural (e.g., "only checkout.py may call Razorpay writes"), use an AST-based check that walks the imports of every file under `app/agents/` and asserts.
- Use import-linter or a custom AST walker.

**Done when:** all invariant tests pass; deliberately breaking one (in a scratch branch) makes the correct test fail.

---

## Phase 5 — LangGraph agents (Layer 2)

### Step 23 — Graph state schema + prompt loader

**Goal:** Shared state definition and prompt-template loading.

**Files:** `backend/app/agents/__init__.py`, `backend/app/agents/state.py`, `backend/app/agents/prompts/__init__.py`, `backend/app/agents/prompts/loader.py`, `backend/app/agents/prompts/router.md` (empty placeholder for now).

**Details:**
- `AgentState` (Pydantic model or `TypedDict` — pick one, LangGraph supports both): `session_id`, `trace_id`, `messages`, `cart`, `current_intent`, `discovery_results`, `recommendations`, `pending_action`, `guardrail_decision`, `final_response`.
- `loader.py`: `load_prompt(name)` reads `agents/prompts/{name}.md` and returns a string. Cache in memory at startup.

**Done when:** `AgentState()` instantiates cleanly and `load_prompt("router")` returns the placeholder content.

### Step 24 — Router node

**Goal:** Classify intent and pick the next node.

**Files:** `backend/app/agents/router.py`, `backend/app/agents/prompts/router.md`.

**Details:**
- Async function `router_node(state: AgentState) -> AgentState` that populates `state.current_intent` from `{"discover", "recommend", "cart", "checkout", "support"}`.
- Uses Claude via `langchain-anthropic` with a small model (`claude-haiku-4-5`). Prompt is few-shot with examples of each intent.
- Every LLM call logs an `agent.router.llm_call` audit event.

**Done when:** unit tests with fixture messages classify correctly; an obviously-checkout message routes to `checkout`.

### Step 25 — Discovery node

**Goal:** Search the catalog, return validated results.

**Files:** `backend/app/agents/discovery.py`, `backend/app/agents/prompts/discovery.md`.

**Details:**
- Flow: extract query and filters from `state.messages` using the LLM → call `VectorStore.search` → validate every returned product exists in Postgres and is in stock → attach to `state.discovery_results` → generate a natural-language response.
- If validation removes a product (i.e., LLM referenced a non-existent SKU), log an `agent.discovery.hallucination_caught` audit event.
- Cannot call Razorpay or modify the cart.

**Done when:** an integration test with the seeded catalog finds a keyboard when asked for one.

### Step 26 — Cart manager node

**Goal:** Deterministic add/remove/total operations.

**Files:** `backend/app/agents/cart_manager.py`.

**Details:**
- Pure operations: `add_item(cart, sku, qty)`, `remove_item(cart, sku)`, `compute_total(cart) -> int (paise)`.
- LLM only used to translate user's message into an operation (e.g., "remove the second one" → `remove_item(cart, sku=<second's sku>)`). Never used for arithmetic.
- Persists the cart to Redis via `session_store`.
- Cannot call Razorpay.

**Done when:** tests verify cart math is exact (no floating-point rounding), and add/remove operations survive a session roundtrip.

### Step 27 — Checkout node — the only money node

**Goal:** Convert a confirmed cart into a paid order.

**Files:** `backend/app/agents/checkout.py`, `backend/app/agents/prompts/checkout.md`.

**Details:**
- Flow: read cart from state → construct `ProposedAction(action_type="create_order", amount_paise=total, ...)` → call `guardrails.gate_money_action(...)` passing a closure that invokes `razorpay_client.create_order` → on success, generate a payment link (also via `gate_money_action`) → attach the checkout URL to `state.final_response`.
- On guardrail denial: return a graceful message to the user (`state.final_response`) explaining what was blocked, using the reason trace. Cart is preserved for the user to modify.
- On payment failure after retries: automatically fall back to `create_payment_link` (also gated). This is the failure-handled-gracefully demo.
- The ONLY import from `integrations/razorpay_client.py` in the entire `agents/` package is here.

**Done when:** end-to-end test with a mocked Razorpay client completes a purchase; a second test simulating three timeouts falls back to a payment link; a third test hitting the per-transaction cap gets denied gracefully.

### Step 28 — Recommender node

**Goal:** Suggest upsell/cross-sell items post-add-to-cart.

**Files:** `backend/app/agents/recommender.py`, `backend/app/agents/prompts/recommender.md`.

**Details:**
- Given the cart, use vector search to find complementary items (query = concatenated cart item descriptions), filter out items already in cart, rank by category-affinity heuristics.
- Return top 3 with a one-sentence reason each.
- LLM only produces the framing sentence, not the ranking.

**Done when:** adding a keyboard to cart surfaces a mouse or a keycap set as a recommendation.

### Step 29 — Support / fallback node

**Goal:** Handle intents the other nodes couldn't.

**Files:** `backend/app/agents/support.py`, `backend/app/agents/prompts/support.md`.

**Details:**
- Catches unclassified intents, offers to hand off to a (mocked) human, and never touches money.
- Should always be a graceful terminal node — no infinite loops back to the router.

**Done when:** an off-topic message ("what's the weather?") routes here and gets a polite non-answer.

### Step 30 — Graph wiring

**Goal:** Assemble all nodes into a LangGraph state machine.

**Files:** `backend/app/agents/graph.py`.

**Details:**
- Build `StateGraph(AgentState)`. Entry point = router. Router's conditional edge routes to discovery, cart_manager, checkout, recommender, or support based on `current_intent`. Discovery and cart_manager loop back to router (to allow follow-up turns). Checkout is terminal. Support is terminal.
- Compile the graph and export `chat_graph` as a module-level singleton.

**Done when:** invoking `chat_graph.ainvoke({"messages": [...]})` with a scripted conversation returns a final state, and inspection shows every node was reachable.

---

## Phase 6 — API + chat endpoint

### Step 31 — Chat endpoint

**Goal:** HTTP interface to the agent graph.

**Files:** `backend/app/api/chat.py`, edits to `main.py`.

**Details:**
- `POST /chat` with body `{session_id, message}`. If `session_id` is null, generate a new one.
- Loads session state from Redis, appends the new message, invokes `chat_graph.ainvoke`, persists new state, returns `{session_id, trace_id, response, cart, pending_approval_id?}`.
- Streams responses via SSE if the client sends `Accept: text/event-stream`.

**Done when:** `curl -X POST /chat` with a discovery message returns product results and a `trace_id` retrievable via `/audit/{trace_id}`.

### Step 32 — Merchant config endpoint

**Goal:** Read and update policy from the merchant console.

**Files:** `backend/app/api/merchant.py` (extend from Step 19).

**Details:**
- `GET /merchant/{merchant_id}/config` and `PUT /merchant/{merchant_id}/config`.
- `GET /merchant/{merchant_id}/approvals?status=pending` for the approval queue.
- Simple API key auth via `X-Merchant-Key` header.

**Done when:** updating the per-transaction cap from the API affects subsequent guardrail decisions.

### Step 33 — WebSocket for real-time approvals

**Goal:** Push new pending approvals to the merchant console.

**Files:** `backend/app/api/ws.py`, edits to `main.py`.

**Details:**
- `WS /ws/merchant/{merchant_id}` streams approval events (new pending, resolved).
- Backed by Redis pub/sub (same channel the confirmation gate publishes to in Step 19).

**Done when:** creating a pending approval via a chat that hits the gate pushes a message to a connected WebSocket client within 100ms.

---

## Phase 7 — MCP server

### Step 34 — MCP server scaffold

**Goal:** Stand up an MCP server sharing the backend's services.

**Files:** `backend/app/mcp_server/__init__.py`, `backend/app/mcp_server/server.py`.

**Details:**
- Use the official Python MCP SDK.
- Server runs on a separate port (`stdio` for local testing, HTTP for the demo).
- Imports the SAME `chat_graph`, `session_store`, and `guardrails` used by the chat endpoint — no parallel implementation.

**Done when:** an MCP client can list tools even if no tools are registered yet.

### Step 35 — MCP tools

**Goal:** Expose the merchant catalog and checkout to external AI buyers.

**Files:** `backend/app/mcp_server/tools.py`.

**Details:**
- Tools: `search_catalog(query, filters, limit)`, `get_product(sku)`, `create_cart() -> cart_id`, `add_to_cart(cart_id, sku, qty)`, `remove_from_cart(cart_id, sku)`, `checkout_cart(cart_id, customer_email)`.
- Every mutating tool routes through the same `guardrails.gate_money_action` path. Same audit trail.
- Tool descriptions written for LLM consumption — precise input/output schemas.

**Done when:** connecting Claude Desktop (or any MCP client) to the server, asking it to shop, and watching it complete a purchase produces an audit trail indistinguishable from a chat-widget purchase.

---

## Phase 8 — Frontend

### Step 36 — Frontend scaffold

**Goal:** Vite + React + Tailwind + TypeScript, three placeholder routes.

**Files:** `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tailwind.config.js`, `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/routes/{Chat,Merchant,Audit}.tsx`.

**Details:**
- React Router with routes `/`, `/merchant`, `/audit/:traceId`.
- Shared API client in `frontend/src/lib/api.ts`.
- Environment variable for backend URL via `VITE_API_URL`.

**Done when:** `npm run dev` boots and all three placeholder routes render.

### Step 37 — Chat widget

**Goal:** Shopper UI that talks to `/chat`.

**Files:** `frontend/src/routes/Chat.tsx` and supporting components under `frontend/src/components/chat/`.

**Details:**
- Message list, input box, streaming responses.
- Product cards rendered when discovery results appear in the response.
- Cart summary sidebar.
- Every agent response has an expandable "why?" pill showing the reasoning summary from the reason trace.
- If a pending approval is returned, show a "waiting for merchant approval" banner.

**Done when:** a demo user can complete a purchase end-to-end in the widget with test-mode Razorpay checkout.

### Step 38 — Merchant console

**Goal:** Policy config + live approval queue.

**Files:** `frontend/src/routes/Merchant.tsx`, supporting components.

**Details:**
- Form to view and update `MerchantConfig`.
- Table of pending approvals with approve/deny buttons.
- WebSocket connection to `/ws/merchant/demo` for real-time updates.

**Done when:** approving via the console unblocks a chat waiting on the confirmation gate within one second.

### Step 39 — Audit viewer

**Goal:** Visual timeline of a trace_id. This is the demo killer.

**Files:** `frontend/src/routes/Audit.tsx`, supporting components.

**Details:**
- Given `/audit/:traceId`, fetch `/audit/{trace_id}` and render events as a vertical timeline.
- Color-code by `actor`. Expand-on-click for full payload. Show relative timestamps.
- Add a "download JSON" button for the raw trace.

**Done when:** entering a real `trace_id` shows the same events as `replay_audit.py` output, but rendered nicely.

---

## Phase 9 — Failure demos

### Step 40 — Timeout + fallback scenario

**Goal:** Deliberately reproducible payment-timeout demo.

**Files:** `backend/tests/test_failure_modes.py`, `backend/scripts/demo_timeout.py`.

**Details:**
- Add a `SIMULATE_RAZORPAY_TIMEOUT=orders:3` env var. When set, the Razorpay client raises `httpx.TimeoutException` for the next N calls to the matching endpoint.
- Script runs a full checkout with this flag, showing: attempt 1 → timeout, attempt 2 → timeout, attempt 3 → timeout, fallback to payment link → success. Same idempotency key throughout.
- Test asserts no double-charge occurred (only one successful create_order OR one payment link, never both).

**Done when:** `python -m scripts.demo_timeout` produces a clean audit trail that shows the failure and recovery.

### Step 41 — Policy denial scenario

**Goal:** Cap-exceeded checkout, handled gracefully.

**Files:** additions to `test_failure_modes.py`, `backend/scripts/demo_denial.py`.

**Details:**
- Set per-transaction cap to ₹500, attempt a ₹5000 checkout, verify user gets a clear message ("This exceeds the per-transaction limit set by the merchant. You can reduce the cart or contact the merchant to increase your limit."), cart is preserved, no Razorpay call was made.

**Done when:** `python -m scripts.demo_denial` runs cleanly and the resulting audit trail shows the denial reason.

### Step 42 — Hallucination catch scenario

**Goal:** Discovery agent proposes a non-existent SKU; validator catches it.

**Files:** additions to `test_failure_modes.py`, `backend/scripts/demo_hallucination.py`.

**Details:**
- Force the discovery LLM (via a test seam) to return a fake SKU. The validation layer in `agents/discovery.py` removes it, logs the hallucination event, and the agent re-searches.
- Assert the fake SKU never reaches the user.

**Done when:** the script demonstrates the self-correction visibly.

---

## Phase 10 — Submission

### Step 43 — README + demo script

**Goal:** Documentation a stranger can follow.

**Files:** `README.md` (rewrite), `docs/demo_script.md`.

**Details:**
- `README.md`: what this is, tech stack, quick start (`docker compose up`, seed, index, run backend, run frontend), links to `ARCHITECTURE.md` and the demo video.
- `docs/demo_script.md`: the exact 5-minute video beats with timings (matches the outline in the old plan.md — problem → happy path → MCP demo → failure demo → architecture).

**Done when:** a friend can `git clone` and reach a working local demo in under 15 minutes following only the README.

### Step 44 — Record pitch video

**Goal:** 5-minute video for submission.

**Files:** none in the repo — video hosted on YouTube unlisted or Loom.

**Details:**
- Follow `docs/demo_script.md` beats.
- Screen-record with clear narration. Show the audit viewer prominently during the happy path AND the failure demo — the audit trail IS the pitch.
- Do NOT show real API keys on screen.

**Done when:** video uploaded, link ready to paste into submission form.

### Step 45 — Submission prep

**Goal:** Repo public, submission form filled out.

**Details:**
- Make repo public. Verify `.env` is gitignored and no keys are in history (`git log -p | grep -i rzp_`).
- Add MIT LICENSE.
- Final smoke test: fresh clone → `docker compose up` → seed → run → demo works.
- Fill out the buildathon application form with repo URL and video URL.

**Done when:** submission confirmation received.

---

## Checklist for reviewing Claude Code's work

Before merging any step, verify:

- [ ] The step's **Done when** condition is met.
- [ ] No new imports from `integrations/razorpay_client.py` outside `agents/checkout.py` or `guardrails/`.
- [ ] No new LLM calls in a code path that computes money amounts, evaluates policy, or generates idempotency keys.
- [ ] Every new file has a docstring naming its architecture layer.
- [ ] Every new HTTP endpoint returns a `trace_id`.
- [ ] Every new money path has an audit-log entry test.
- [ ] `pytest tests/test_guardrails_invariants.py` still passes.