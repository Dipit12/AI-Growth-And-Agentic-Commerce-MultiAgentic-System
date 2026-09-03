"""Deterministic string-to-UUID coercion, shared by the confirmation gate and every agent node that
needs to turn a session_id string into a UUID for audit logging."""

import uuid


def to_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, value)
