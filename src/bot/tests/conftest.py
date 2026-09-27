"""Shared e2e/integration fixtures: a real Postgres in a test container."""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A real Postgres 17, reachable over asyncpg."""
    with PostgresContainer("postgres:17", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_url)
    yield engine
    await engine.dispose()
