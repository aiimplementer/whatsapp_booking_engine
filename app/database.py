from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,  # Neon endpoints can idle-suspend on the free tier; this
                         # detects a dead connection and reconnects instead of erroring.
    pool_size=5,
    max_overflow=5,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for every model."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Plain session dependency — no tenant context set.
    Use only for tenant-agnostic operations (e.g. the tenant signup endpoint
    itself, which by definition happens before a tenant_id exists to scope to).
    """
    async with AsyncSessionLocal() as session:
        yield session


async def get_db_for_tenant(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Session dependency that sets the RLS session variable for the duration of
    the request's transaction. Every table protected by the tenant_isolation
    policy in the schema checks this value, so this is the ONE place a bug
    here would leak data across tenants — keep this function boring and obvious.

    Uses set_config(..., true) (the "SET LOCAL" equivalent, but as a normal
    function call so it can take a bind parameter — plain `SET LOCAL x = :y`
    isn't valid over asyncpg's parameterized protocol) rather than a plain
    SET, specifically so it's transaction-scoped and never leaks onto a
    pooled connection reused by someone else.

    Registered as an `after_begin` listener rather than run once up front:
    routers commonly do `db.commit()` mid-request and then keep using the
    session (e.g. to `db.refresh()` the row they just wrote). Each commit
    ends the current transaction and SQLAlchemy silently autobegins a new
    one on the next statement — a one-off SET LOCAL before the first
    statement would not cover that second transaction. Listening for
    after_begin re-applies it every time, first transaction included.
    """
    async with AsyncSessionLocal() as session:

        def _apply_tenant_context(sess, transaction, connection):
            connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )

        event.listen(session.sync_session, "after_begin", _apply_tenant_context)
        try:
            yield session
        finally:
            event.remove(session.sync_session, "after_begin", _apply_tenant_context)
