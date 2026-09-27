"""Shared e2e/integration fixtures: a real Postgres in a test container."""

from collections.abc import AsyncIterator, Iterator

import pytest
from app.db.migrate import run_migrations
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A real Postgres 17, reachable over asyncpg."""
    with PostgresContainer("postgres:17", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema(postgres_url: str) -> None:
    """Give every test the migrated schema, as the bot would have on start."""
    run_migrations(postgres_url)


@pytest.fixture(scope="session")
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One transaction-shaped session over the shared migrated schema."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
