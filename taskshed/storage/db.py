"""Engine / session factory helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> AsyncEngine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            # share one in-memory DB across all connections (tests)
            kwargs["poolclass"] = StaticPool
    return create_async_engine(url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
