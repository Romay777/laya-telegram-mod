"""Seam: the migration runner. `run_migrations` brings a real Postgres to head."""

from app.db.migrate import run_migrations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

SKELETON_TABLES = ["bot_user", "fsm_state"]


async def test_run_migrations_creates_schema(engine: AsyncEngine, postgres_url: str) -> None:
    run_migrations(postgres_url)

    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        tables = {row[0] for row in rows}

    for table in SKELETON_TABLES:
        assert table in tables, f"migration did not create {table}"


async def test_run_migrations_is_idempotent(postgres_url: str) -> None:
    run_migrations(postgres_url)
    run_migrations(postgres_url)  # second start must not fail or duplicate anything
