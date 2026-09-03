"""Pydantic read-schema for audit events, shared between the API and the replay script. Layer 5."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

REDACTED_KEYS = {"card_number", "cvv", "token", "secret"}


def redact_payload(payload: dict) -> dict:
    """Recursively replaces any dict value whose key matches a sensitive field name with '***'.
    Invariant #6 in CLAUDE.md: no PII, card numbers, CVVs, or auth tokens ever leave the audit store.
    """
    redacted: dict = {}
    for key, value in payload.items():
        if key.lower() in REDACTED_KEYS:
            redacted[key] = "***"
        elif isinstance(value, dict):
            redacted[key] = redact_payload(value)
        elif isinstance(value, list):
            redacted[key] = [redact_payload(v) if isinstance(v, dict) else v for v in value]
        else:
            redacted[key] = value
    return redacted


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    session_id: uuid.UUID
    timestamp: datetime
    actor: str
    event_type: str
    payload: dict
    reason: str | None
