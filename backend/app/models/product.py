"""Product catalog model + schemas. Layer 4 (integrations & data) — read-only from agents' perspective."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


def _json_type() -> JSON:
    """JSONB on Postgres, plain JSON on SQLite (used by tests) — same column, portable type."""
    return JSON().with_variant(JSONB(), "postgresql")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    embedding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductCreate(BaseModel):
    sku: str
    name: str
    description: str
    price_paise: int = Field(ge=0)
    stock: int = Field(ge=0)
    category: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    description: str
    price_paise: int
    stock: int
    category: str
    attributes: dict[str, Any]
