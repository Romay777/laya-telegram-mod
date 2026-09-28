"""The Menu navigator: renders screens into the Admin's single Menu message (§13).

The Menu lives in one message per Admin, whose id is stored as
`bot_user.menu_message_id` and so survives restarts. Every screen change
edits that message; when it can't be edited any more (deleted, too old), the
navigator sends a new one and removes the old where possible.
"""

import contextlib
from collections.abc import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser
from app.domain.linking import LinkingProblems
from app.i18n import translator_for
from app.menu.screen import Screen
from app.menu.screens import (
    add_chat_screen,
    chat_screen,
    enter_chat_screen,
    home_screen,
    how_it_works_screen,
    language_screen,
    link_expired_screen,
    link_failed_screen,
    linked_chat_screen,
)
from app.menu.screens.home import ChatSummary


class MenuNavigator:
    def __init__(self, core: BaseCore) -> None:
        self.core = core

    async def show_language_screen(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        telegram_language_code: str | None,
        locale: str,
    ) -> None:
        highlight = telegram_language_code if telegram_language_code in ("ru", "en") else None
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=language_screen(translator_for(self.core, locale), highlight),
        )

    async def show_home(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        locale: str,
        chats: Sequence[ChatSummary] = (),
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=home_screen(translator_for(self.core, locale), chats),
        )

    async def show_chat(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_title: str | None,
        mode: str,
        backend: str,
        sensitivity: str,
        locale: str,
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=chat_screen(
                translator_for(self.core, locale),
                chat_title,
                mode=mode,
                backend=backend,
                sensitivity=sensitivity,
            ),
        )

    async def show_how_it_works(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, locale: str
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=how_it_works_screen(translator_for(self.core, locale)),
        )

    async def show_add_chat(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, url: str, locale: str
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=add_chat_screen(translator_for(self.core, locale), url),
        )

    async def show_enter_chat(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, locale: str
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=enter_chat_screen(translator_for(self.core, locale)),
        )

    async def show_enter_chat_again(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, locale: str
    ) -> None:
        """The same prompt, after an input that named no chat the bot can see."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=enter_chat_screen(translator_for(self.core, locale), error=True),
        )

    async def show_linked_chat(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_title: str | None,
        mode: str,
        backend: str,
        sensitivity: str,
        locale: str,
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=linked_chat_screen(
                translator_for(self.core, locale),
                chat_title,
                mode=mode,
                backend=backend,
                sensitivity=sensitivity,
            ),
        )

    async def show_link_failed(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_title: str | None,
        chat_id: int,
        problems: LinkingProblems,
        locale: str,
        fallback: bool = False,
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=link_failed_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                problems=problems,
                fallback=fallback,
            ),
        )

    async def show_link_expired(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, locale: str
    ) -> None:
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=link_expired_screen(translator_for(self.core, locale)),
        )

    async def _show(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, screen: Screen
    ) -> None:
        """Edit the stored Menu message into the screen, recreating it if it is gone."""
        if user.menu_message_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=user.user_id,
                    message_id=user.menu_message_id,
                    text=screen.text,
                    reply_markup=screen.reply_markup,
                )
                return
            except TelegramBadRequest:
                await self._recreate(bot=bot, session=session, user=user, screen=screen)
                return
        await self._recreate(bot=bot, session=session, user=user, screen=screen)

    async def _recreate(
        self, *, bot: Bot, session: AsyncSession, user: BotUser, screen: Screen
    ) -> None:
        stale_message_id = user.menu_message_id
        sent = await bot.send_message(
            chat_id=user.user_id,
            text=screen.text,
            reply_markup=screen.reply_markup,
        )
        user.menu_message_id = sent.message_id
        await session.flush()
        if stale_message_id is None:
            return
        # Already gone; removing the old message is best effort.
        with contextlib.suppress(TelegramBadRequest):
            await bot.delete_message(chat_id=user.user_id, message_id=stale_message_id)
