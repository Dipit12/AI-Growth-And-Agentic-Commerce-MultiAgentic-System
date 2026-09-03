"""Shared pytest fixtures: async app client, mocked Redis, mocked Postgres session.

POSTGRES_URL is pointed at a file-backed SQLite DB for the whole test session (set here, before any
`app.*` module is first imported, so `app.db.session`'s module-level engine picks it up). That lets
API-level tests exercise the real app wiring (main.py's routers, app/api/deps.py's shared
AuditLogger/session-factory references) without a running Postgres — file-based SQLite behaves
correctly under normal connection pooling, unlike `:memory:`.
"""

import os
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "razorpay_buildathon_test.db"
_TEST_DB_PATH.unlink(missing_ok=True)

os.environ.setdefault("RAZORPAY_KEY_ID", "rzp_test_fixture")
os.environ.setdefault("RAZORPAY_KEY_SECRET", "fixture_secret")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("POSTGRES_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    from app.db.base import Base
    from app.db.session import engine
    from app.main import app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator["fakeredis.aioredis.FakeRedis"]:  # type: ignore[name-defined]
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield redis
    finally:
        await redis.aclose()


@pytest_asyncio.fixture
async def async_session() -> AsyncGenerator["AsyncSession", None]:  # type: ignore[name-defined]
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.db.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
