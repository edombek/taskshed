"""Engine / session factory helpers."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> AsyncEngine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            # share one in-memory DB across all connections (tests)
            kwargs["poolclass"] = StaticPool

    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # SQLite ignores the schema's ON DELETE CASCADE unless asked — without
        # this, deleting a task would leave its occurrences and completions
        # behind as orphans
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_pragma_on_connect(dbapi_con, _conn_record):  # noqa: ANN001
            dbapi_con.execute("PRAGMA foreign_keys=ON")

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
