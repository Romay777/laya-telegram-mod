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
from aiogram.types import MessageEntity
from aiogram_i18n.cores.base import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotUser
from app.domain.linking import LinkingProblems
from app.domain.template import Validation
from app.i18n import translator_for
from app.menu.screen import Screen
from app.menu.screens import (
    add_chat_screen,
    categories_screen,
    chat_language_screen,
    chat_screen,
    enter_chat_screen,
    home_screen,
    how_it_works_screen,
    ladder_screen,
    ladder_step_screen,
    language_screen,
    link_expired_screen,
    link_failed_screen,
    linked_chat_screen,
    my_alerts_screen,
    notice_template_screens,
    sensitivity_screen,
    settings_screen,
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
        chats: Sequence[ChatSummary],
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
        chat_id: int,
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
                chat_id=chat_id,
                mode=mode,
                backend=backend,
                sensitivity=sensitivity,
            ),
        )

    async def show_settings(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        mode: str,
        locale: str,
    ) -> None:
        """⚙️ Settings of one chat (§13); this ticket's screen is the Mode switch."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=settings_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                mode=mode,
            ),
        )

    async def show_my_alerts(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        alert_mode: str,
        locale: str,
    ) -> None:
        """My alerts of one chat (§9): All, Appeals only, or Off."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=my_alerts_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                alert_mode=alert_mode,
            ),
        )

    async def show_categories(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        enabled: Sequence[str],
        locale: str,
    ) -> None:
        """Categories of one chat (§13): spam, ads and insult, on or off."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=categories_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                enabled=tuple(enabled),
            ),
        )

    async def show_sensitivity(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        sensitivity: str,
        locale: str,
    ) -> None:
        """Sensitivity of one chat (§13): Lenient, Balanced or Strict."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=sensitivity_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                sensitivity=sensitivity,
            ),
        )

    async def show_chat_language(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        chat_language: str,
        locale: str,
    ) -> None:
        """Chat Language of one chat (§15, §13): 🇷🇺 or 🇬🇧."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=chat_language_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                chat_language=chat_language,
            ),
        )

    async def show_ladder(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        ladder: Sequence[int],
        expiry_seconds: int | None,
        locale: str,
    ) -> None:
        """The Penalty Ladder of one chat (§6, §13): Steps, Add/Remove, Expiry."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=ladder_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                ladder=tuple(ladder),
                expiry_seconds=expiry_seconds,
            ),
        )

    async def show_ladder_step(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        index: int,
        seconds: int,
        locale: str,
    ) -> None:
        """One Step's duration presets (§6, §13)."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=ladder_step_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
                index=index,
                seconds=seconds,
            ),
        )

    async def show_notice_template(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        chat_language: str,
        template_text: str | None,
        locale: str,
    ) -> None:
        """The Notice Template of one chat (§14): the current one, or the default."""
        core = self.core
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=notice_template_screens.template_screen(
                translator_for(core, locale),
                translator_for(core, chat_language),
                chat_title,
                chat_id=chat_id,
                template_text=template_text,
            ),
        )

    async def show_template_edit(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_title: str | None,
        locale: str,
    ) -> None:
        """Edit (§14): the prompt to send a formatted message."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=notice_template_screens.edit_screen(
                translator_for(self.core, locale), chat_title, chat_id=chat_id
            ),
        )

    async def show_template_preview(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        user: BotUser,
        chat_id: int,
        chat_language: str,
        text: str,
        entities: Sequence[MessageEntity],
        validation: Validation,
        locale: str,
    ) -> None:
        """The Preview (§14): sample values in the Chat Language, then the choice."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=notice_template_screens.preview_screen(
                translator_for(self.core, locale),
                translator_for(self.core, chat_language),
                None,
                chat_id=chat_id,
                text=text,
                entities=entities,
                validation=validation,
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
        chat_id: int,
        chat_title: str | None,
        mode: str,
        backend: str,
        sensitivity: str,
        locale: str,
    ) -> None:
        """✅ {chat} linked, then the Auto-moderation choice (§13)."""
        await self._show(
            bot=bot,
            session=session,
            user=user,
            screen=linked_chat_screen(
                translator_for(self.core, locale),
                chat_title,
                chat_id=chat_id,
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
