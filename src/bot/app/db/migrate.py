"""Programmatic Alembic runner.

The bot applies all migrations on start, before polling begins (§16). The same
function is exercised by the integration tests against a real Postgres.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def run_migrations(database_url: str) -> None:
    """Run `alembic upgrade head` against `database_url` (a sync or async URL).

    Safe to call from a running event loop: the upgrade itself needs its own
    loop (the asyncpg-driven Alembic env), so it runs on a worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _upgrade(database_url)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_upgrade, database_url).result()


def _upgrade(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
