from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all Noodle ORM models."""


def _create_engine():
    url = settings.database_url
    # SQLite uses NullPool (no persistent connections); PostgreSQL gets a
    # tunable connection pool so operators can right-size it in .env.
    if url.startswith("sqlite"):
        return create_async_engine(url, poolclass=NullPool)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
    )


engine = _create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Tenancy enforcement (no-op while multi_tenancy_enabled is off): org-scopes
# every ORM SELECT and sets the Postgres RLS GUC per transaction. Imported
# late — tenancy reads Base from this module.
from app.tenancy import install_org_filter  # noqa: E402

install_org_filter()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
