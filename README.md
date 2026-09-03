# Agentic Commerce — Razorpay Buildathon 2026 (Track 01)

An agentic commerce platform with one brain and two faces: a conversational checkout agent (chat
widget) and an MCP server exposing the same catalog and checkout to external AI buyers (Claude,
ChatGPT, agentic browsers). Both faces route through a single guardrails layer that gates every
money action and writes to a single append-only audit log.

- **What this is and why it's built this way:** [`CLAUDE.md`](CLAUDE.md)
- **Architecture diagram + explanation:** [`ARCHITECTURE.md`](ARCHITECTURE.md), deeper dive in
  [`docs/architecture.md`](docs/architecture.md)
- **API reference:** [`docs/api_reference.md`](docs/api_reference.md)
- **Failure modes in detail:** [`docs/failure_modes.md`](docs/failure_modes.md)
- **Demo video:** _link goes here once recorded — see [`docs/demo_script.md`](docs/demo_script.md)_

## Tech stack

Python 3.11+ (FastAPI, LangGraph, Anthropic Claude, Razorpay SDK test-mode, SQLAlchemy async,
Qdrant, Redis) on the backend; React + Vite + TypeScript + Tailwind on the frontend; the official
Python MCP SDK for the agent-facing server; Postgres/Redis/Qdrant via Docker Compose for local dev.

## Quick start

### 1. Bring up the datastores

```bash
docker compose up -d
docker compose ps   # postgres, redis, qdrant should all show healthy
```

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows — use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"

cp ../.env.example ../.env    # fill in RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET (test mode) and ANTHROPIC_API_KEY

alembic upgrade head           # if you've added migrations — see "Database migrations" below
python -m scripts.seed_catalog # ~50 synthetic products
python -m scripts.index_catalog # embeds them into Qdrant

uvicorn app.main:app --reload  # http://localhost:8000
```

Run the test suite (no live Postgres/Redis/Qdrant required — see
[`docs/architecture.md`](docs/architecture.md#testing-strategy-without-live-infrastructure)):

```bash
pytest
mypy app/guardrails/ app/agents/checkout.py   # strict typing on the money-critical paths
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173, expects VITE_API_URL=http://localhost:8000 (see .env.example)
```

Visit `/` for the shopper chat, `/merchant` for the merchant console (default key
`demo-merchant-key`, see `.env.example`), `/audit/:traceId` for the audit viewer.

### 4. MCP server (optional)

```bash
cd backend
python -m app.mcp_server.server          # stdio, for Claude Desktop / local MCP clients
python -m app.mcp_server.server --http   # streamable-http, for a remote demo
```

### 5. See the guardrails in action

```bash
cd backend
python -m scripts.demo_timeout          # 3 simulated Razorpay timeouts -> automatic fallback
python -m scripts.demo_denial           # over-cap checkout, denied gracefully, cart preserved
python -m scripts.demo_hallucination    # a fabricated SKU caught before it reaches the user

python -m scripts.replay_audit <trace_id> --verbose   # pretty-print any trace from the terminal
```

## Repo layout

See the tree in [`PLAN.md`](PLAN.md) for the intended structure this repo follows — `backend/app/`
mirrors the five architecture layers 1:1 (`agents/` = orchestrator, `guardrails/` = money firewall,
`integrations/` = external services, `mcp_server/` + `api/` = surfaces, `audit/` = observability).

## Non-negotiable invariants

This project enforces nine invariants (only `checkout.py` can call Razorpay writes, every write
goes through guardrails, idempotency keys are mandatory, audit records bracket every money action,
test mode is enforced in code, no PII in prompts/logs, deterministic ops stay deterministic, the
confirmation gate can't be bypassed, failures are always visible) — see
[`CLAUDE.md`](CLAUDE.md#non-negotiable-invariants) for the full list and
`backend/tests/test_guardrails_invariants.py` for how each one is checked, several via an
AST walk over the actual source tree rather than convention alone.

## Known limitations of this environment

This particular build was assembled in a sandbox without Docker, a local Redis/Postgres/Qdrant, or
real Razorpay/Anthropic credentials — the test suite works around that (file-backed SQLite +
fakeredis, see `docs/architecture.md`), but a handful of things could only be verified up to the
external API boundary rather than fully end-to-end: `scripts/demo_timeout.py` and
`scripts/demo_denial.py` need a real `ANTHROPIC_API_KEY` to generate their final user-facing message
(the guardrail logic itself is independently unit-tested), and nothing in this repo has made a real
call to live Razorpay test-mode endpoints. Run the quick start above with real credentials to
exercise those paths fully.
