"""Async SQLAlchemy engine and session helpers."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, committed by the handler."""
    async with sessionmaker()() as session:
        yield session


ADDED_COLUMNS = [
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS kind varchar NOT NULL DEFAULT 'webapp'",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS public boolean NOT NULL DEFAULT false",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS description varchar NOT NULL DEFAULT ''",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS queue_paused boolean NOT NULL DEFAULT false",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS style varchar",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS palette varchar",
    "ALTER TABLE turns ADD COLUMN IF NOT EXISTS queued_at timestamptz NOT NULL DEFAULT now()",
]


async def create_all() -> None:
    from .models import Base

    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all never alters an existing table: columns added after a database
        # was first created are added here, idempotently.
        for ddl in ADDED_COLUMNS:
            await conn.execute(text(ddl))
