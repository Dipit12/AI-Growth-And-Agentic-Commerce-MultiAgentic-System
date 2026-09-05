"""Async engine, sessionmaker, and the FastAPI `get_session` dependency. Layer 4 (integrations & data)."""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.POSTGRES_URL, pool_pre_ping=True)

if engine.dialect.name == "sqlite":
    # File-backed SQLite (the Docker-free local-dev / test substitute for Postgres) defaults to a
    # single-writer lock with a near-zero busy timeout — under this app's normal concurrency (the
    # audit logger's background drain task writing while a request handler reads/writes too), that
    # surfaces as "database is locked" errors that would never happen against real Postgres. WAL
    # mode lets readers and a writer proceed concurrently; a real busy_timeout makes a genuine
    # writer-vs-writer collision retry briefly instead of failing immediately.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: object, connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
