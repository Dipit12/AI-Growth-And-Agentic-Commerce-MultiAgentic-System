"""Declarative base every SQLAlchemy model inherits from. Layer 4 (integrations & data)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
