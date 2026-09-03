# CLAUDE.md

Context for AI assistants (Claude Code, Cursor, etc.) working on this repository. Read this before generating or editing code.

## What this project is

An agentic commerce platform built for the **Razorpay AI Buildathon 2026 — Track 01 (AI Growth & Agentic Commerce)**. It has two faces sharing one brain:

1. A **conversational checkout agent** that lets a human shopper discover, cart, and pay for products entirely through chat.
2. An **MCP (Model Context Protocol) server** that exposes the same merchant catalog and checkout capabilities to external AI buyers (Claude, ChatGPT, agentic browsers) as callable tools.

Both faces route through a single guardrails layer that gates every money action and writes to a single audit log. This project is submitted as a public repo + 5-minute pitch video + architecture document.

## Tech stack

- **Language:** Python 3.11+ (backend), TypeScript + React (frontend)
- **Agent framework:** LangGraph (state-machine orchestration of specialized nodes)
- **LLM:** Anthropic Claude via API for routing and generation; local Llama via Ollama as fallback for cost-sensitive nodes
- **Payments:** Razorpay Python SDK, **test mode only**
- **Vector store:** Qdrant (product embeddings for semantic catalog search)
- **Session store:** Redis (cart state, conversation memory, per-session policy counters)
- **Audit store:** Postgres, append-only table with a shared `trace_id`
- **API:** FastAPI
- **MCP:** official Python MCP SDK
- **Frontend:** React + Vite + Tailwind; a chat widget, a merchant console, and an audit-trail viewer
- **Local dev:** Docker Compose bringing up Postgres, Redis, Qdrant

## Architecture in one paragraph

Five layers, top to bottom: **Surfaces** (chat widget, MCP server, merchant console) → **LangGraph orchestrator** (router, discovery, recommender, cart manager, checkout) → **Guardrails** (policy engine, confirmation gate, idempotency + retry, explainer) → **Integrations & data** (Razorpay test APIs, Qdrant, Redis) → **Audit log** (append-only Postgres, written to from every layer above). See `ARCHITECTURE.md` for the diagram and full explanation.

## Non-negotiable invariants

These are the rules that separate this from a hackathon toy. Every code change should be checked against them; if you're about to break one, stop and ask.

1. **Only `agents/checkout.py` may call Razorpay write endpoints.** Discovery, recommender, cart manager, and the MCP tools can read prices and inventory, but they must never create an order, capture a payment, issue a refund, or trigger a payment link directly. Any such call from another module is a bug.

2. **Every Razorpay write goes through `guardrails/`.** The checkout agent invokes guardrails; guardrails invoke the Razorpay client. There is no path where the agent constructs a request and hands it to `razorpay_client.py` without the guardrails layer in between. Do not "shortcut for testing."

3. **Idempotency keys are mandatory on every write.** Every `create_order`, `capture_payment`, `refund`, and `create_payment_link` call carries a deterministic idempotency key derived from `(session_id, cart_hash, action_type)`. A retry after a timeout must produce the same key.

4. **Every money action produces an audit record before the API call and after the response.** The pre-call record captures intent + reasoning + guardrail decision; the post-call record captures the Razorpay response. Both share the same `trace_id`. If either write fails, the money action is aborted.

5. **Test mode is enforced in code, not just config.** `razorpay_client.py` asserts the API key starts with `rzp_test_` on initialization and refuses to load a production key. Do not weaken this assertion.

6. **No PII, card numbers, CVVs, or auth tokens in LLM prompts or audit logs.** Sensitive fields are referenced by opaque handles (customer IDs, saved-method IDs). If you find yourself passing raw card data through an agent, redesign the flow — Razorpay's hosted checkout or tokenization should own that data.

7. **Deterministic operations stay deterministic.** Cart totals, tax math, policy checks (spending caps, per-transaction limits), and idempotency-key generation are pure Python. LLMs never do arithmetic on money and never decide whether an action is within policy.

8. **The confirmation gate is the LLM's boss, not the other way around.** If the policy engine says an action requires human approval, the agent cannot "explain its way out" — the request is paused, the merchant console is notified, and the flow waits. The LLM is never given a tool that bypasses this.

9. **Failures must be visible.** Every retry, every fallback (e.g., native flow → payment link), every guardrail denial is logged with reason. A failed payment must produce an audit trail as complete as a successful one.

## Coding conventions

- **Async everywhere** in the backend — FastAPI is async, LangGraph nodes are async, all I/O is awaited. No sync `requests` calls.
- **Pydantic models** for every payload crossing a module boundary. Type checking is enforced with `mypy --strict` on the `guardrails/` and `agents/checkout.py` paths at minimum.
- **Structured logging** via `structlog`, always including `trace_id`, `session_id`, and `node_name`. Never `print`.
- **Prompt templates live in `agents/prompts/`** as `.md` files loaded at startup. Do not inline prompts as Python string literals inside node functions.
- **Tests colocated with intent**, not implementation. Every guardrail rule has a test in `tests/test_guardrails.py` that describes the invariant it enforces. Every failure mode has a test in `tests/test_failure_modes.py`.
- **No secrets in code, ever.** `.env` for local, environment variables in deployment. `.env.example` is committed; `.env` is gitignored.

## Common tasks

- **Add a new agent node:** create `agents/<name>.py` implementing the node protocol, register it in `agents/graph.py`, add its prompt template to `agents/prompts/`, and write a router case in `agents/router.py`. If the node touches money in any way, the answer is no — extend `checkout.py` or add a guardrail instead.
- **Add a new MCP tool:** define it in `mcp_server/tools.py`, wire it to the same underlying service the chat agent uses (not a parallel implementation), and confirm it respects the same guardrails.
- **Add a new policy rule:** extend `guardrails/policy_engine.py` with the rule, add a merchant-console field to configure it, and write both a passing and a failing test for it.
- **Run the stack locally:** `docker compose up -d` for the datastores, `uvicorn app.main:app --reload` for the API, `npm run dev` in `frontend/` for the UIs. See `README.md` for full setup.
- **Replay an audit trail:** `python scripts/replay_audit.py <trace_id>` prints every event in order — use this to debug and to build the demo video.

## Before submitting a change

- Run `pytest tests/test_guardrails.py tests/test_failure_modes.py` — these must be green.
- Confirm your change did not add a Razorpay call outside `checkout.py` + `guardrails/`.
- Confirm your change did not add an LLM to a deterministic computation.
- If your change is user-visible, update `docs/demo_script.md` so the pitch video stays accurate.

## Out of scope (do not build)

- Production KYC, real onboarding flows, or anything requiring live keys.
- Offensive fraud tooling of any kind — this project is defense-adjacent even though it isn't a Track 02 submission.
- Any "auto-execute large payments without confirmation" mode, even behind a flag.