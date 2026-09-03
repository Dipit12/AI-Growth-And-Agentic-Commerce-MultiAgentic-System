"""Shared singletons for the API layer. Layer 1 (surfaces). The audit logger's background drain
task is started/stopped from main.py's lifespan, since it needs a running event loop.
"""

from app.audit.logger import AuditLogger
from app.db.session import async_session_factory
from app.integrations.session_store import SessionStore

audit_logger = AuditLogger(async_session_factory)
session_store = SessionStore()


def get_audit_logger() -> AuditLogger:
    return audit_logger


def get_session_store() -> SessionStore:
    return session_store
