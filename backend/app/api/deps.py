"""Shared singletons for the API layer. Layer 1 (surfaces). The audit logger is the same
process-wide singleton app/agents/*.py nodes default to (app/audit/singleton.py) — re-exported here
so existing `from app.api.deps import get_audit_logger` call sites keep working unchanged. Its
background drain task is started/stopped from main.py's lifespan, since it needs a running event loop.
"""

from app.audit.singleton import audit_logger, get_audit_logger
from app.integrations.session_store import SessionStore

session_store = SessionStore()

__all__ = ["audit_logger", "get_audit_logger", "session_store", "get_session_store"]


def get_session_store() -> SessionStore:
    return session_store
