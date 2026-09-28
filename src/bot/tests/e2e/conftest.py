"""Per-test isolation for the e2e suite: every test starts from an empty DB.

The e2e tests share one Postgres container; Home now lists every Linked Chat
in the instance, so a leftover `chat` row from the previous test would leak
into the next test's screens. Truncating the data tables (the seeded
`category` rows stay) gives every test the fresh instance it asserts on.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture(autouse=True)
async def clean_database(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        # chat CASCADEs into admin_subscription and chat_category.
        await conn.execute(text("TRUNCATE bot_user, chat, link_intent, fsm_state CASCADE"))
    yield
    await engine.dispose()
