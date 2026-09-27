"""Router-level middlewares: DB session per update, and the Admin's bot_user row."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Chat, TelegramObject, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.db.repositories.users import BotUserRepository


class DbSessionMiddleware(BaseMiddleware):
    """One AsyncSession per update, committed when the handler succeeds."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self.session_maker = session_maker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_maker() as session:
            data["session"] = session
            result = await handler(event, data)
            await session.commit()
            return result


class BotUserMiddleware(BaseMiddleware):
    """Loads (or, on first contact, creates) the bot_user row of a private chat.

    Runs after DbSessionMiddleware, before the i18n middleware, so both the
    handlers and the locale resolution can use `data["bot_user"]`.
    """

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user, chat = _update_sender(event)
        session: AsyncSession | None = data.get("session")
        data["telegram_language_code"] = from_user.language_code if from_user else None
        if (
            session is not None
            and from_user is not None
            and chat is not None
            and chat.type == "private"
        ):
            data["bot_user"] = await BotUserRepository(session).get_or_create(
                from_user.id, started_at=self.clock.now()
            )
        return await handler(event, data)


def _update_sender(event: TelegramObject) -> tuple[User | None, Chat | None]:
    """The user and chat an Update comes from, across the update types we handle."""
    if not isinstance(event, Update):
        return None, None
    for name in ("message", "edited_message", "callback_query", "my_chat_member", "chat_member"):
        obj = getattr(event, name)
        if obj is None:
            continue
        from_user: User | None = getattr(obj, "from_user", None)
        chat: Chat | None = getattr(obj, "chat", None)
        if chat is None and name == "callback_query":
            message = getattr(obj, "message", None)
            chat = getattr(message, "chat", None)
        return from_user, chat
    return None, None
