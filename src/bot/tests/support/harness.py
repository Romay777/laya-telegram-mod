"""The e2e app: real wiring, fake Telegram.

Everything downstream of the Dispatcher is the real app (Postgres FSM
storage, i18n, handlers); the only fakes are the Bot API session, which
records every outgoing call, and the Clock (§17).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from app.clock import FakeClock
from app.main import build_dispatcher, build_i18n_middleware
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.support.fake_session import FakeBotSession
from tests.support.updates import Update

TEST_BOT_TOKEN = "42:test-token"
FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class TestApp:
    __test__ = False  # a harness, not a test class: keep pytest from collecting it

    dispatcher: Dispatcher
    bot: Bot
    session: FakeBotSession
    session_maker: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    clock: FakeClock
    _update_id: int = 0

    async def feed(self, update: Update) -> None:
        self._update_id += 1
        # aiogram types are frozen: rebuild the Update with the next id.
        update = Update(
            update_id=self._update_id,
            **update.model_dump(exclude={"update_id"}, exclude_none=True),
        )
        await self.dispatcher.feed_update(self.bot, update)

    async def aclose(self) -> None:
        await self.engine.dispose()


async def build_app(postgres_url: str, *, prompt_delete_after_s: float = 600.0) -> TestApp:
    i18n = build_i18n_middleware()
    await i18n.core.startup()  # the Dispatcher's startup hook does this in production

    engine = create_async_engine(postgres_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    clock = FakeClock(FIXED_NOW)
    dispatcher = build_dispatcher(
        session_maker=session_maker,
        i18n=i18n,
        clock=clock,
        # The default is 600 s (§10); tests shrink it so the self-deleting
        # group prompt actually deletes inside the test.
        prompt_delete_after_s=prompt_delete_after_s,
    )

    session = FakeBotSession()
    bot = Bot(TEST_BOT_TOKEN, session=session)
    return TestApp(
        dispatcher=dispatcher,
        bot=bot,
        session=session,
        session_maker=session_maker,
        engine=engine,
        clock=clock,
    )


@asynccontextmanager
async def app_fixture(
    postgres_url: str, *, prompt_delete_after_s: float = 600.0
) -> AsyncIterator[TestApp]:
    app = await build_app(postgres_url, prompt_delete_after_s=prompt_delete_after_s)
    try:
        yield app
    finally:
        await app.aclose()
