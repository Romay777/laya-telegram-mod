"""Wiring: settings, DB, Bot, Dispatcher (§2)."""

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram_i18n import I18nMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clock import Clock, SystemClock
from app.config import Settings
from app.db.fsm_storage import PostgresStorage
from app.db.migrate import run_migrations
from app.i18n.middleware import build_i18n_middleware
from app.menu.navigator import MenuNavigator
from app.telegram.handlers.private import create_private_router
from app.telegram.middlewares import BotUserMiddleware, DbSessionMiddleware

# §16: long polling receives exactly these update types.
ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "my_chat_member", "chat_member"]

logger = logging.getLogger(__name__)


def build_session_maker(database_url: str) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(create_async_engine(database_url), expire_on_commit=False)


def build_dispatcher(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    i18n: I18nMiddleware,
    clock: Clock,
) -> Dispatcher:
    navigator = MenuNavigator(core=i18n.core)
    dispatcher = Dispatcher(storage=PostgresStorage(session_maker))
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_maker))
    dispatcher.update.outer_middleware(BotUserMiddleware(clock=clock))
    i18n.setup(dispatcher)  # locale resolution runs after the DB middlewares
    dispatcher.include_router(create_private_router())
    dispatcher["navigator"] = navigator
    dispatcher["clock"] = clock
    return dispatcher


def build_bot(token: str, session: BaseSession | None = None) -> Bot:
    return Bot(token=token, session=session) if session is not None else Bot(token=token)


async def run() -> None:
    settings = Settings.load()
    logging.basicConfig(level=settings.log_level)

    run_migrations(settings.database_url)  # applied on start, before polling (§16)

    i18n = build_i18n_middleware()
    dispatcher = build_dispatcher(
        session_maker=build_session_maker(settings.database_url),
        i18n=i18n,
        clock=SystemClock(),
    )
    bot = build_bot(settings.bot_token)
    logger.info("starting polling with allowed_updates=%s", ALLOWED_UPDATES)
    try:
        await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        await i18n.core.shutdown()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run())
