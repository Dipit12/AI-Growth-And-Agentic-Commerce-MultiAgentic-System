"""Typed settings loaded from environment / .env. Cross-cutting — used by every layer."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: Literal["dev", "test", "production"] = "dev"

    RAZORPAY_KEY_ID: str = "rzp_test_placeholder"
    RAZORPAY_KEY_SECRET: str = "placeholder_secret"

    ANTHROPIC_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""

    POSTGRES_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/razorpay_buildathon"
    REDIS_URL: str = "redis://localhost:6379/0"
    QDRANT_URL: str = "http://localhost:6333"

    MERCHANT_API_KEY: str = "demo-merchant-key"

    SIMULATE_RAZORPAY_TIMEOUT: str = Field(
        default="",
        description="Debug knob for demo_timeout.py, e.g. 'orders:3'. Never read outside the razorpay client.",
    )

    ROUTER_MODEL: str = "claude-haiku-4-5"

    @property
    def is_dev(self) -> bool:
        return self.ENV == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
