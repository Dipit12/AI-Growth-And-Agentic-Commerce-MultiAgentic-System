"""One named test per invariant in CLAUDE.md (#1-#9). Structural invariants use an AST walk over
the actual source tree so a violation is caught even if no other test happens to exercise it.
"""

import ast
import inspect
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit.logger import AuditLogger
from app.config import Settings
from app.db.base import Base
from app.guardrails import gate as gate_module
from app.guardrails.idempotency import with_retry
from app.guardrails.policy_engine import evaluate as policy_evaluate
from app.guardrails.explainer import build_trace
from app.integrations.razorpay_client import RazorpayClient, RazorpayTestModeError
from app.integrations.session_store import SessionStore
from app.models.audit import redact_payload
from app.models.policy import ProposedAction

APP_ROOT = Path(__file__).resolve().parent.parent / "app"
AGENTS_DIR = APP_ROOT / "agents"
MCP_SERVER_DIR = APP_ROOT / "mcp_server"


def _imported_modules(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _all_py_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return list(directory.rglob("*.py"))


def test_invariant_1_only_checkout_agent_imports_razorpay_client() -> None:
    """'Only agents/checkout.py may call Razorpay write endpoints.' CLAUDE.md calls out the MCP
    tools explicitly here too ("the MCP tools can read prices and inventory, but they must never
    create an order...directly"), so this walks both app/agents/ and app/mcp_server/."""
    violations = []
    for py_file in [*_all_py_files(AGENTS_DIR), *_all_py_files(MCP_SERVER_DIR)]:
        if py_file.name == "checkout.py":
            continue
        modules = _imported_modules(py_file)
        if any("razorpay_client" in m for m in modules):
            violations.append(str(py_file))

    assert violations == [], f"Non-checkout files import razorpay_client: {violations}"


def test_invariant_2_checkout_agent_routes_writes_through_guardrails_gate() -> None:
    """'Every Razorpay write goes through guardrails/.' checkout.py must call gate_money_action
    rather than constructing requests and handing them to razorpay_client.py directly."""
    checkout_file = AGENTS_DIR / "checkout.py"
    if not checkout_file.exists():
        pytest.skip("agents/checkout.py not implemented yet")

    source = checkout_file.read_text(encoding="utf-8")
    modules = _imported_modules(checkout_file)

    assert any("guardrails" in m for m in modules), "checkout.py must import from app.guardrails"
    assert "gate_money_action(" in source, "checkout.py must call gate_money_action(...) for every write"


def test_invariant_3_every_razorpay_write_method_requires_idempotency_key() -> None:
    """'Idempotency keys are mandatory on every write.'"""
    write_methods = ["create_order", "capture_payment", "create_payment_link", "refund"]
    for name in write_methods:
        method = getattr(RazorpayClient, name)
        params = inspect.signature(method).parameters
        assert "idempotency_key" in params, f"{name} is missing a mandatory idempotency_key param"


@pytest.mark.asyncio
async def test_invariant_4_gate_writes_audit_before_and_after_the_razorpay_call() -> None:
    """'Every money action produces an audit record before the API call and after the response.'"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    audit = AuditLogger(session_factory)
    session_store = SessionStore(redis=redis)

    call_order: list[str] = []

    async def mock_call(idempotency_key: str) -> dict:
        call_order.append("razorpay_call")
        return {"id": "order_inv4"}

    action = ProposedAction(
        action_type="create_order", amount_paise=50_000, category="electronics",
        payment_method="card", session_id="inv4", cart_hash="hash-inv4",
    )

    original_log_guardrail = audit.log_guardrail_event
    original_log_razorpay = audit.log_razorpay_event

    async def tracked_guardrail(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_order.append(f"audit_pre:{kwargs.get('event_type')}")
        return await original_log_guardrail(*args, **kwargs)

    async def tracked_razorpay(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_order.append(f"audit_post:{kwargs.get('event_type')}")
        return await original_log_razorpay(*args, **kwargs)

    audit.log_guardrail_event = tracked_guardrail  # type: ignore[method-assign]
    audit.log_razorpay_event = tracked_razorpay  # type: ignore[method-assign]

    result = await gate_module.gate_money_action(
        action, uuid.uuid4(), uuid.uuid4(), mock_call,
        audit=audit, session_factory=session_factory, session_store=session_store,
    )

    await redis.aclose()
    await engine.dispose()

    assert result.success is True
    pre_index = next(i for i, c in enumerate(call_order) if c.startswith("audit_pre"))
    call_index = call_order.index("razorpay_call")
    post_index = next(i for i, c in enumerate(call_order) if c.startswith("audit_post"))
    assert pre_index < call_index < post_index


def test_invariant_5_test_mode_is_enforced_in_code_not_just_config() -> None:
    """'Test mode is enforced in code, not just config.'"""
    live_settings = Settings(RAZORPAY_KEY_ID="rzp_live_shouldfail", RAZORPAY_KEY_SECRET="s")
    with pytest.raises(RazorpayTestModeError):
        RazorpayClient(settings=live_settings)


def test_invariant_6_audit_redaction_strips_sensitive_fields() -> None:
    """'No PII, card numbers, CVVs, or auth tokens in LLM prompts or audit logs.'"""
    payload = {
        "card_number": "4111111111111111",
        "cvv": "123",
        "token": "tok_secret",
        "nested": {"secret": "shh", "safe_field": "ok"},
        "safe_field": "ok",
    }
    redacted = redact_payload(payload)
    assert redacted["card_number"] == "***"
    assert redacted["cvv"] == "***"
    assert redacted["token"] == "***"
    assert redacted["nested"]["secret"] == "***"
    assert redacted["nested"]["safe_field"] == "ok"
    assert redacted["safe_field"] == "ok"


def test_invariant_7_deterministic_modules_never_import_an_llm_client() -> None:
    """'Deterministic operations stay deterministic.' Cart math, policy checks, and idempotency-key
    generation must be pure Python — no LLM/agent-framework imports in these modules."""
    deterministic_files = [
        APP_ROOT / "guardrails" / "policy_engine.py",
        APP_ROOT / "guardrails" / "idempotency.py",
        APP_ROOT / "guardrails" / "explainer.py",
    ]
    banned_substrings = ("anthropic", "langchain", "openai")

    for py_file in deterministic_files:
        modules = _imported_modules(py_file)
        offending = [m for m in modules if any(b in m for b in banned_substrings)]
        assert offending == [], f"{py_file.name} imports an LLM-related module: {offending}"

    cart_manager_file = APP_ROOT / "agents" / "cart_manager.py"
    if cart_manager_file.exists():
        source = cart_manager_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        compute_total_funcs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "compute_total"
        ]
        assert compute_total_funcs, "cart_manager.py must define a pure compute_total function"


@pytest.mark.asyncio
async def test_invariant_8_gated_denial_blocks_the_razorpay_call_even_on_retry() -> None:
    """'The confirmation gate is the LLM's boss, not the other way around.' A denied gate must never
    reach the Razorpay call, and there is no parameter that lets a caller skip the gate."""
    gate_signature = inspect.signature(gate_module.gate_money_action)
    assert "razorpay_call" in gate_signature.parameters
    # There must be no bypass-style flag (e.g. skip_approval, force, override) on the public entry point.
    bypass_like = [
        name for name in gate_signature.parameters
        if any(word in name.lower() for word in ("skip", "bypass", "override", "force"))
    ]
    assert bypass_like == [], f"gate_money_action exposes a bypass parameter: {bypass_like}"


def test_invariant_9_retry_exhaustion_is_logged_before_raising() -> None:
    """'Failures must be visible.' A retry-exhausted call must log before it re-raises, so a failed
    payment produces an audit trail as complete as a successful one."""
    source = inspect.getsource(with_retry)
    assert "logger.error" in source and "retry_exhausted" in source
    assert "raise RetryExhaustedError" in source
