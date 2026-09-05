"""Process-wide shared AuditLogger. Layer 5 (audit).

Every agent node defaults its `audit` parameter to this singleton rather than `None` or a throwaway
`AuditLogger(...)` instance. That distinction matters: LangGraph invokes each node with only the
state argument (no `audit=` kwarg), so a `None` default meant router/discovery/cart_manager/
recommender/support never logged anything in production — only checkout_node's events landed,
because money events persist synchronously and happened to not need the background drain task. A
fresh throwaway `AuditLogger` instance doesn't fix that either: its queue would never be drained
unless something calls `.start()` on that exact instance, and it's garbage-collected after the node
returns. This singleton's `.start()` is called once, in main.py's lifespan (or explicitly by
standalone entry points like the MCP server), and its background drain task then lives for the
whole process.
"""

from app.audit.logger import AuditLogger
from app.db.session import async_session_factory

audit_logger = AuditLogger(async_session_factory)


def get_audit_logger() -> AuditLogger:
    return audit_logger
