"""Engine / session factory with SQLite pragmas suited for one writer + concurrent readers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import Base


def create_engine(settings: Settings) -> AsyncEngine:
    url = make_url(settings.database_url)
    kwargs: dict[str, Any] = {}
    is_sqlite = url.get_backend_name() == "sqlite"
    in_memory = is_sqlite and url.database in (None, "", ":memory:")

    if is_sqlite and not in_memory and url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    if in_memory:
        # One shared connection, otherwise every session would see its own empty database.
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_async_engine(settings.database_url, **kwargs)

    if is_sqlite:
        busy_timeout = settings.sqlite_busy_timeout_ms

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            if not in_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout)}")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
